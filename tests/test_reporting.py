import contextlib
import hashlib
import io
import json
import os
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import main

from .report_schema import validate_report
from .support import LocalHTTPTestCase


class TestReporting(LocalHTTPTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = self.root / "app.json"
        self.report = self.root / "report.json"
        self.revision = "a" * 40
        (self.root / "build-revision").write_text(self.revision)
        self.provenance_patch = patch("src.lib.provenance.CHECKER_ROOT", self.root)
        self.provenance_patch.start()
        self.addCleanup(self.provenance_patch.stop)

    def source(self, version="2", *, same=False):
        headers = {"Last-Modified": "Fri, 06 Nov 2026 12:00:00 GMT"}
        old_url = self.serve(b"old", headers=headers)
        new_url = old_url if same else self.serve(b"new", headers=headers)
        endpoint = self.serve(
            json.dumps({"version": version, "url": new_url}),
            content_type="application/json",
        )
        return {
            "type": "file",
            "url": old_url,
            "sha256": hashlib.sha256(b"old").hexdigest(),
            "x-checker-data": {
                "type": "json",
                "url": endpoint,
                "version-query": ".version",
                "url-query": ".url",
            },
        }

    def write(self, sources):
        self.manifest.write_text(json.dumps({"name": "app", "sources": sources}))

    async def run_report(self, *options):
        args = main.parse_cli_args(
            ["--report-json", str(self.report), *options, str(self.manifest)]
        )
        result = await main.run_with_args(args)
        report = json.loads(self.report.read_text())
        validate_report(report)
        self.assertEqual(
            report["checker"],
            {"revision": self.revision, "source": "build", "dirty": None},
        )
        return result, report

    async def test_mixed_results_with_referenced_source(self):
        current = self.source(same=True)
        unchecked = self.source()
        del unchecked["x-checker-data"]
        failed = self.source()
        failed["x-checker-data"]["version-query"] = ".missing"
        referenced = self.root / "source.json"
        referenced.write_text(json.dumps(self.source()))
        self.write([current, unchecked, failed, "source.json"])
        result, report = await self.run_report()
        self.assertGreater(result[1], 0)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["schema_version"], 1)
        sources = report["sources"]
        self.assertEqual(
            [s["status"] for s in sources],
            ["checked", "unchecked", "failed", "update_available"],
        )
        self.assertEqual(sources[1]["reason"], "no_update_checker")
        self.assertEqual(sources[2]["errors"][0]["type"], "CheckerQueryError")
        self.assertEqual(sources[0]["source_pointer"], "/sources/0")
        self.assertEqual(sources[3]["manifest"], "source.json")
        self.assertEqual(sources[3]["source_pointer"], "")

    async def test_shared_modules_exclusion_is_not_a_report_failure(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "update-index",
                "--add",
                "--cacheinfo",
                "160000",
                "a" * 40,
                "shared-modules",
            ],
            check=True,
        )
        self.write([self.source(same=True), "shared-modules/source.json"])
        result, report = await self.run_report()
        self.assertEqual(result[1], 0)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["errors"], [])
        self.assertNotIn("exclusions", report)
        self.assertEqual(report["sources"][0]["status"], "checked")

        self.write([self.source(), "shared-modules/source.json"])
        before = self.manifest.read_bytes()
        result, report = await self.run_report(
            "--edit-only", "--require-important-update"
        )
        self.assertFalse(result[2])
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["sources"][0]["status"], "blocked")
        self.assertEqual(self.manifest.read_bytes(), before)

    async def test_missing_shared_modules_without_gitlink_remains_an_error(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.write(["shared-modules/source.json"])
        _, report = await self.run_report()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["errors"][0]["type"], "ManifestFileOpenError")

    async def test_edit_preserves_pre_update_snapshot(self):
        source = self.source()
        self.write([source])
        result, report = await self.run_report("--edit-only")
        self.assertTrue(result[2])
        self.assertEqual(report["delivery"], "edited")
        entry = report["sources"][0]
        self.assertEqual(entry["current"]["url"], source["url"])
        self.assertNotEqual(entry["candidate"]["url"], source["url"])
        updated = json.loads(self.manifest.read_text())["sources"][0]
        self.assertEqual(updated["url"], entry["candidate"]["url"])

    async def test_blocked_group_and_filtered_source(self):
        sources = [self.source("2"), self.source("3")]
        for source in sources:
            source["x-checker-data"]["update-group"] = "app"
        self.write(sources)
        _, report = await self.run_report("--edit-only")
        self.assertEqual(report["delivery"], "no_changes")
        self.assertTrue(all(s["status"] == "blocked" for s in report["sources"]))
        self.assertTrue(all(s["reason"] == "update_group" for s in report["sources"]))
        _, report = await self.run_report("--filter-type", "git")
        self.assertTrue(all(s["reason"] == "filtered" for s in report["sources"]))

    async def test_policy_block(self):
        current = self.source(same=True)
        current["x-checker-data"]["is-important"] = True
        self.write([current, self.source()])
        _, report = await self.run_report("--require-important-update")
        self.assertEqual(report["sources"][1]["reason"], "update_policy")

    async def test_load_failure_and_lint_report(self):
        self.manifest.write_text("{}")
        result, report = await self.run_report()
        self.assertEqual(result, (0, 1, False))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["sources"], [])
        self.assertTrue(report["errors"])
        _, report = await self.run_report("--lint")
        self.assertEqual(report["mode"], "lint")
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.requests, [])

    async def test_git_decoding_failure_does_not_interrupt_lint(self):
        (self.root / "build-revision").unlink()
        for args in (
            ["init"],
            [
                "-c",
                "user.name=Test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "--allow-empty",
                "-m",
                "Initial",
            ],
            ["config", "core.quotePath", "false"],
        ):
            subprocess.run(
                ["git", "-C", str(self.root), *args],
                check=True,
                capture_output=True,
            )
        with open(os.fsencode(self.root) + b"/file-\xff", "wb"):
            pass
        self.write([])
        args = main.parse_cli_args(
            ["--lint", "--report-json", str(self.report), str(self.manifest)]
        )
        result = await main.run_with_args(args)
        report = json.loads(self.report.read_text())
        validate_report(report)
        self.assertEqual(result, (0, 0, False))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(
            report["checker"],
            {"revision": None, "source": "unknown", "dirty": None},
        )

    async def test_publication_failure_keeps_detection_results(self):
        self.write([self.source()])
        with (
            patch("src.main.get_manifest_git_checkout", return_value=self.root),
            patch("src.main.ensure_git_safe_directory"),
            patch("src.main.commit_changes", return_value=object()),
            patch("src.main.open_pr", return_value=False),
        ):
            result, report = await self.run_report("--update")
        self.assertEqual(result, (-1, -1, False))
        self.assertEqual(report["delivery"], "failed")
        self.assertEqual(report["sources"][0]["status"], "update_available")

    @contextlib.contextmanager
    def mock_publication(self, *, existing=True, automerge=False):
        self.write([self.source()])
        (self.root / "flathub.json").write_text(
            json.dumps({"automerge-flathubbot-prs": automerge})
        )
        repo = MagicMock(full_name="flathub/app", default_branch="master")
        repo.owner.login = "flathub"
        pr = MagicMock(
            state="open",
            mergeable=True,
            html_url="https://github.com/flathub/app/pull/123",
        )
        pr.is_merged.return_value = False
        pr.merge.return_value.merged = True
        commit = pr.head.repo.get_commit.return_value
        commit.get_combined_status.return_value.state = "success"
        repo.get_pulls.return_value = [pr] if existing else []
        repo.create_pull.return_value = pr
        push = MagicMock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        open_pr = main.open_pr

        def publish(*args, **kwargs):
            with (
                patch("src.main.Github") as github,
                patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}),
                patch(
                    "src.main.subprocess.check_output",
                    return_value=b"https://github.com/flathub/app",
                ),
                patch("src.main.subprocess.run", push),
            ):
                github.return_value.get_repo.return_value = repo
                return open_pr(*args, **kwargs)

        change = main.CommittedChanges("Update app", None, "tree", "update", "master")
        with (
            patch("src.main.get_manifest_git_checkout", return_value=self.root),
            patch("src.main.ensure_git_safe_directory"),
            patch("src.main.commit_changes", return_value=change),
            patch("src.main.open_pr", side_effect=publish),
        ):
            yield repo, pr, push

    async def test_existing_pr_outcomes(self):
        for state, merged, outcome in (
            ("open", False, "existing_open"),
            ("closed", False, "existing_closed"),
            ("closed", True, "existing_merged"),
        ):
            with (
                self.subTest(outcome=outcome),
                self.mock_publication() as (repo, pr, push),
            ):
                pr.state = state
                pr.is_merged.return_value = merged
                result, report = await self.run_report("--update")
                self.assertEqual(result, (1, 0, True))
                self.assertEqual(report["delivery"], "pr_handled")
                self.assertEqual(
                    report["pull_request"], {"url": pr.html_url, "outcome": outcome}
                )
                push.assert_not_called()
                repo.create_pull.assert_not_called()
                pr.edit.assert_not_called()
                pr.merge.assert_not_called()
                pr.create_issue_comment.assert_not_called()

    async def test_created_pr_outcome(self):
        with self.mock_publication(existing=False) as (repo, pr, push):
            result, report = await self.run_report("--update")
            self.assertEqual(result, (1, 0, True))
            self.assertEqual(report["delivery"], "pr_handled")
            self.assertEqual(
                report["pull_request"], {"url": pr.html_url, "outcome": "created"}
            )
            push.assert_called_once()
            repo.create_pull.assert_called_once()
            pr.create_issue_comment.assert_not_called()

    async def test_merged_pr_outcome(self):
        with self.mock_publication(automerge=True) as (repo, pr, push):
            result, report = await self.run_report("--update")
            self.assertEqual(result, (1, 0, True))
            self.assertEqual(report["delivery"], "pr_handled")
            self.assertEqual(
                report["pull_request"], {"url": pr.html_url, "outcome": "merged"}
            )
            pr.merge.assert_called_once_with(merge_method="rebase")
            pr.create_issue_comment.assert_called_once()
            push.assert_not_called()
            repo.create_pull.assert_not_called()

    async def test_pr_outcome_survives_failure(self):
        for operation, existing, automerge, outcome in (
            ("create_pull", False, False, None),
            ("set_labels", False, False, "created"),
            ("merge", True, True, "existing_open"),
            ("get_git_ref", True, True, "merged"),
        ):
            with (
                self.subTest(operation=operation),
                self.mock_publication(existing=existing, automerge=automerge) as (
                    repo,
                    pr,
                    push,
                ),
            ):
                target = repo if operation in ("create_pull", "get_git_ref") else pr
                getattr(target, operation).side_effect = RuntimeError(operation)
                with self.assertRaisesRegex(RuntimeError, operation):
                    await self.run_report("--update", "--pr-labels", "update")
                report = json.loads(self.report.read_text())
                validate_report(report)
                self.assertEqual(report["delivery"], "failed")
                self.assertEqual(report["status"], "failed")
                self.assertEqual(report["sources"][0]["status"], "update_available")
                if outcome is None:
                    self.assertNotIn("pull_request", report)
                else:
                    self.assertEqual(
                        report["pull_request"], {"url": pr.html_url, "outcome": outcome}
                    )
                if outcome != "merged":
                    pr.create_issue_comment.assert_not_called()

    async def test_rejected_merge_keeps_open_pr_outcome(self):
        with self.mock_publication(automerge=True) as (_, pr, _):
            pr.merge.return_value.merged = False
            pr.merge.return_value.message = "rejected"
            with self.assertRaisesRegex(RuntimeError, "rejected"):
                await self.run_report("--update")
            report = json.loads(self.report.read_text())
            validate_report(report)
            self.assertEqual(report["delivery"], "failed")
            self.assertEqual(
                report["pull_request"], {"url": pr.html_url, "outcome": "existing_open"}
            )
            pr.create_issue_comment.assert_not_called()

    async def test_rejected_push_has_no_pr_outcome(self):
        with self.mock_publication(existing=False) as (repo, _, push):
            push.return_value.returncode = 1
            result, report = await self.run_report("--update")
            self.assertEqual(result, (-1, -1, False))
            self.assertEqual(report["delivery"], "failed")
            self.assertNotIn("pull_request", report)
            repo.create_pull.assert_not_called()

    async def test_unexpected_failure_writes_report_and_propagates(self):
        self.write([self.source()])
        with (
            patch(
                "src.main.manifest.ManifestChecker.check",
                side_effect=RuntimeError("bug"),
            ),
            self.assertRaisesRegex(RuntimeError, "bug"),
        ):
            await self.run_report()
        report = json.loads(self.report.read_text())
        validate_report(report)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["errors"][0]["message"], "bug")
        self.assertIn("finished_at", report)

    async def test_skipped_module_is_not_a_successful_report(self):
        self.manifest.write_text(
            json.dumps(
                {
                    "name": "app",
                    "modules": ["missing.json"],
                    "sources": [],
                }
            )
        )
        _, report = await self.run_report()
        self.assertEqual(report["status"], "failed")
        self.assertIn("missing.json", report["errors"][0]["message"])

    async def test_reporting_preserves_console_output_and_exit_result(self):
        sources = [self.source("2"), self.source("3")]
        for source in sources:
            source["x-checker-data"]["update-group"] = "app"
        self.write(sources)
        outputs = []
        for options in ([], ["--report-json", str(self.report)]):
            args = main.parse_cli_args([*options, str(self.manifest)])
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout), self.assertLogs("src") as logs:
                result = await main.run_with_args(args)
            outputs.append((result, stdout.getvalue(), Counter(logs.output)))
        self.assertEqual(outputs[0], outputs[1])
        self.assertIn("OUTDATED", outputs[1][1])
        self.assertIn("2026-11-06 12:00:00+00:00", outputs[1][1])

    async def test_unchanged_rotating_url_is_checked(self):
        source = self.source(same=True)
        source["x-checker-data"] = {
            "type": "rotating-url",
            "url": source["url"],
        }
        self.write([source])
        _, report = await self.run_report()
        self.assertEqual(report["sources"][0]["status"], "checked")
        self.assertIsNone(report["sources"][0]["candidate"])
