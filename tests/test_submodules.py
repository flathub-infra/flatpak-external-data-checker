import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import main
from src.lib.errors import CheckerError
from src.lib.externaldata import ExternalData
from src.lib.sourceupdates import format_source_updates
from src.lib.submodules import SubmoduleChecker, git
from src.manifest import CheckerOptions, ManifestChecker
from tests.test_reporting import validate_report


class TestSubmodules(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.remote = self.directory / "remote"
        self.root = self.directory / "app"
        environment = patch.dict(
            os.environ,
            {
                "GIT_AUTHOR_NAME": "Test",
                "GIT_AUTHOR_EMAIL": "test@example.com",
                "GIT_COMMITTER_NAME": "Test",
                "GIT_COMMITTER_EMAIL": "test@example.com",
                "GIT_ALLOW_PROTOCOL": "file",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        for repo in (self.remote, self.root):
            repo.mkdir()
            self.git(repo, "init", "--initial-branch=main")
        self.write_module("used.json", {"name": "used", "sources": []})
        (self.remote / "patch.diff").write_text("old patch\n")
        self.old = self.commit(self.remote)
        self.git(self.root, "submodule", "add", str(self.remote), "shared-modules")
        self.shared = self.root / "shared-modules"
        self.manifest = self.root / "app.json"
        self.write_manifest(
            {"id": "org.test.App", "modules": ["shared-modules/used.json"]}
        )
        self.commit(self.root)

    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()

    def commit(self, repo):
        self.git(repo, "add", ".")
        self.git(repo, "commit", "--allow-empty", "-m", "Update files")
        return self.git(repo, "rev-parse", "HEAD")

    def write_module(self, name, contents):
        (self.remote / name).write_text(json.dumps(contents))

    def write_manifest(self, contents):
        self.manifest.write_text(json.dumps(contents))

    async def checker(self, **options):
        checker = ManifestChecker(str(self.manifest), CheckerOptions(**options))
        await checker.check()
        self.assertEqual(checker.get_errors(), [])
        return checker

    async def test_check_is_read_only_and_apply_pins_checked_commit(self):
        self.write_module("used.json", {"name": "new", "sources": []})
        candidate = self.commit(self.remote)
        original = self.manifest.read_bytes()
        checker = await self.checker()
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)
        self.assertEqual(self.git(self.root, "status", "--porcelain"), "")
        self.assertEqual(len(checker.submodule_checker.updates), 1)
        self.assertEqual(main.print_outdated_external_data(checker), 1)
        self.write_module("used.json", {"name": "even-newer"})
        self.commit(self.remote)
        changes = checker.update_manifests()
        self.assertEqual(
            changes,
            [f"Update submodule shared-modules to {candidate[:12]}"],
        )
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), candidate)
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertEqual(checker.source_updates[0].new_version, candidate[:12])
        self.assertEqual(checker.source_updates[0].source, "Submodule shared-modules")
        self.assertEqual(checker.source_updates[0].submodule_files, ("used.json",))
        self.assertEqual(checker.get_errors(), [])

    async def test_unrelated_changes_do_not_trigger_updates(self):
        self.write_module("unused.json", {"name": "unused"})
        self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertEqual(checker.update_manifests(), [])

    def omitted_pin_remote(self, *, changed=True, missing=False):
        if missing:
            (self.remote / "used.json").unlink()
        elif changed:
            self.write_module("used.json", {"name": "new", "sources": []})
        self.git(self.remote, "add", ".")
        tree = self.git(self.remote, "write-tree")
        candidate = self.git(self.remote, "commit-tree", tree, "-m", "New history")
        self.git(self.remote, "update-ref", "refs/heads/main", candidate)
        url = self.remote.as_uri()
        self.git(self.root, "config", "submodule.shared-modules.url", url)

        # Use the upload-pack transport, not local clone's copy of all objects.
        probe = self.directory / "probe"
        self.git(self.directory, "clone", "--no-checkout", "--", url, str(probe))
        self.assertEqual(
            self.git(probe, "rev-parse", "--is-shallow-repository"), "false"
        )
        self.assertEqual(self.git(probe, "rev-parse", "origin/HEAD"), candidate)
        result = subprocess.run(
            ["git", "-C", str(probe), "checkout", "--detach", self.old],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"unable to read tree ({self.old})", result.stderr)
        self.git(probe, "fetch", "--no-tags", "--", url, self.old)
        self.assertEqual(
            self.git(probe, "rev-parse", f"{self.old}^{{commit}}"), self.old
        )
        return candidate

    async def test_omitted_pin_is_fetched_without_changing_app_checkout(self):
        candidate = self.omitted_pin_remote()
        original = self.manifest.read_bytes()
        with patch("src.lib.submodules.git", wraps=git) as calls:
            checker = await self.checker()
        update = checker.submodule_checker.updates[0]
        self.assertEqual((update.current, update.candidate), (self.old, candidate))
        self.assertEqual(update.files, ("used.json",))
        fetches = [
            call.args[1:] for call in calls.call_args_list if call.args[1] == "fetch"
        ]
        self.assertEqual(
            fetches,
            [("fetch", "--no-tags", "--depth=1", "--", self.remote.as_uri(), self.old)],
        )
        clones = [
            call.args[-1] for call in calls.call_args_list if call.args[1] == "clone"
        ]
        self.assertEqual(len(clones), 1)
        self.assertFalse(Path(clones[0]).parent.exists())
        self.assertEqual(self.manifest.read_bytes(), original)
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)
        self.assertEqual(self.git(self.root, "status", "--porcelain"), "")

    async def test_present_pin_does_not_need_fetch(self):
        self.write_module("used.json", {"name": "new"})
        self.commit(self.remote)
        with patch("src.lib.submodules.git", wraps=git) as calls:
            checker = await self.checker()
        self.assertEqual(len(checker.submodule_checker.updates), 1)
        self.assertFalse(any(call.args[1] == "fetch" for call in calls.call_args_list))

    def test_invalid_pin_is_not_used_as_a_git_argument(self):
        checker = SubmoduleChecker(self.root, 1024)
        for current in ("--upload-pack=command", "main", "a" * 39, "a" * 41):
            with (
                self.subTest(current=current),
                patch("src.lib.submodules.git") as calls,
            ):
                with self.assertRaisesRegex(CheckerError, "Invalid submodule commit"):
                    checker._check_submodule(
                        "shared-modules", "shared-modules", current, {}
                    )
                calls.assert_not_called()

    async def test_unavailable_pin_blocks_update(self):
        self.omitted_pin_remote()
        unavailable = "a" * 40
        self.git(
            self.root,
            "update-index",
            "--cacheinfo",
            "160000",
            unavailable,
            "shared-modules",
        )
        original = self.git(self.root, "status", "--porcelain")
        checker = ManifestChecker(str(self.manifest))
        await checker.check()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertIn(
            "Submodule shared-modules: Git fetch failed", str(checker.get_errors()[0])
        )
        self.assertIn(unavailable, str(checker.get_errors()[0]))
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)
        self.assertEqual(self.git(self.root, "status", "--porcelain"), original)

    async def test_pin_fetch_transport_failure_blocks_update(self):
        self.omitted_pin_remote()

        def disconnect_after_clone(directory, *args):
            result = git(directory, *args)
            if args[0] == "clone":
                self.remote.rename(self.directory / "offline")
            return result

        with patch("src.lib.submodules.git", side_effect=disconnect_after_clone):
            checker = ManifestChecker(str(self.manifest))
            await checker.check()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertIn(
            "Submodule shared-modules: Git fetch failed", str(checker.get_errors()[0])
        )
        self.assertIn(
            "does not appear to be a git repository", str(checker.get_errors()[0])
        )
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)
        self.assertEqual(self.git(self.root, "status", "--porcelain"), "")

    async def test_omitted_pin_with_unchanged_snapshot_does_not_update(self):
        self.omitted_pin_remote(changed=False)
        checker = await self.checker()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertEqual(self.git(self.root, "status", "--porcelain"), "")

    async def test_pin_fetch_timeout_is_reported_and_checkout_cleaned_up(self):
        self.omitted_pin_remote()
        checkouts = []

        def timeout_fetch(directory, *args):
            if args[0] == "fetch":
                raise subprocess.TimeoutExpired(["git", *args], 120)
            result = git(directory, *args)
            if args[0] == "clone":
                checkouts.append(Path(args[-1]))
            return result

        with patch("src.lib.submodules.git", side_effect=timeout_fetch):
            checker = ManifestChecker(str(self.manifest))
            await checker.check()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertIn("timed out after 120 seconds", str(checker.get_errors()[0]))
        self.assertEqual(len(checkouts), 1)
        self.assertFalse(checkouts[0].parent.exists())
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)
        self.assertEqual(self.git(self.root, "status", "--porcelain"), "")

    async def test_omitted_pin_does_not_allow_missing_candidate_reference(self):
        self.omitted_pin_remote(missing=True)
        checker = ManifestChecker(str(self.manifest))
        await checker.check()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertIn("Referenced file missing", str(checker.get_errors()[0]))
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)
        self.assertEqual(self.git(self.root, "status", "--porcelain"), "")

    async def test_multiple_changed_components_are_updated_together(self):
        self.write_module("second.json", {"name": "second"})
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.write_manifest(
            {
                "name": "app",
                "modules": ["shared-modules/used.json", "shared-modules/second.json"],
                "sources": [{"type": "patch", "path": "shared-modules/patch.diff"}],
            }
        )
        self.commit(self.root)

        self.write_module("used.json", {"name": "updated-first"})
        self.write_module("second.json", {"name": "updated-second"})
        (self.remote / "patch.diff").write_text("new patch\n")
        self.write_module("unused.json", {"name": "unused"})
        candidate = self.commit(self.remote)

        checker = await self.checker()
        self.assertEqual(len(checker.submodule_checker.updates), 1)
        self.assertEqual(
            checker.submodule_checker.updates[0].files,
            (
                "patch.diff",
                "second.json",
                "used.json",
            ),
        )
        self.assertEqual(
            checker.get_submodule_report()[0]["files"],
            [
                "patch.diff",
                "second.json",
                "used.json",
            ],
        )
        self.assertEqual(
            checker.update_manifests(),
            [f"Update submodule shared-modules to {candidate[:12]}"],
        )
        self.assertIn(
            "### Submodule changes\n\n"
            r"Submodule shared\-modules needs updating because these "
            "files used by the manifest changed:\n\n"
            "- <code>patch\\.diff</code>\n"
            "- <code>second\\.json</code>\n"
            "- <code>used\\.json</code>",
            format_source_updates(checker.source_updates),
        )
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), candidate)
        self.assertEqual(
            json.loads((self.shared / "used.json").read_text())["name"], "updated-first"
        )
        self.assertEqual(
            json.loads((self.shared / "second.json").read_text())["name"],
            "updated-second",
        )
        self.assertEqual((self.shared / "patch.diff").read_text(), "new patch\n")

    async def test_malformed_submodule_manifest_is_reported(self):
        (self.remote / "used.json").write_text("not JSON")
        self.commit(self.remote)
        checker = ManifestChecker(str(self.manifest))
        await checker.check()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertIn(
            "Cannot read submodule manifest used.json", str(checker.get_errors()[0])
        )

    async def test_multiple_submodules_are_updated(self):
        self.git(self.root, "submodule", "add", str(self.remote), "other-modules")
        self.write_manifest(
            {
                "name": "app",
                "modules": ["shared-modules/used.json", "other-modules/used.json"],
            }
        )
        self.commit(self.root)
        self.write_module("used.json", {"name": "updated"})
        candidate = self.commit(self.remote)

        checker = await self.checker()
        self.assertEqual(
            {update.path for update in checker.submodule_checker.updates},
            {"shared-modules", "other-modules"},
        )
        self.assertEqual(len(checker.update_manifests()), 2)
        for path in ("shared-modules", "other-modules"):
            self.assertEqual(self.git(self.root / path, "rev-parse", "HEAD"), candidate)

    async def test_patch_reference_without_module_include(self):
        self.write_manifest(
            {
                "name": "app",
                "sources": [
                    {
                        "type": "patch",
                        "path": "shared-modules/patch.diff",
                    }
                ],
            }
        )
        self.commit(self.root)
        (self.remote / "patch.diff").write_text("new patch\n")
        self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(checker.submodule_checker.updates[0].files, ("patch.diff",))

    async def test_nested_includes_and_local_sources(self):
        self.write_module("used.json", {"name": "used", "modules": ["child.yaml"]})
        (self.remote / "child.yaml").write_text(
            "name: child\nsources:\n  - type: patch\n    paths: [patch.diff]\n"
        )
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.commit(self.root)
        (self.remote / "patch.diff").write_text("new patch\n")
        self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(checker.submodule_checker.updates[0].files, ("patch.diff",))

    async def test_removed_dependency_is_allowed_if_no_longer_referenced(self):
        self.write_module("used.json", {"name": "used", "modules": ["child.json"]})
        self.write_module("child.json", {"name": "child"})
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.commit(self.root)
        self.write_module("used.json", {"name": "used"})
        (self.remote / "child.json").unlink()
        self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(len(checker.submodule_checker.updates), 1)

    async def test_missing_referenced_manifest_blocks_update(self):
        (self.remote / "used.json").unlink()
        self.commit(self.remote)
        checker = ManifestChecker(str(self.manifest))
        await checker.check()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertIn("Referenced file missing", str(checker.get_errors()[0]))

    async def test_dirty_submodule_is_not_overwritten(self):
        self.write_module("used.json", {"name": "new"})
        self.commit(self.remote)
        checker = await self.checker()
        (self.shared / "patch.diff").write_text("local changes\n")
        self.assertEqual(checker.update_manifests(), [])
        self.assertIn("local changes", str(checker.get_errors()[0]))
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)

    async def test_ignored_local_file_is_not_overwritten(self):
        excludes = self.directory / "ignore"
        excludes.write_text("local.txt\n")
        self.git(self.shared, "config", "core.excludesFile", str(excludes))
        local = self.shared / "local.txt"
        local.write_text("local content\n")
        self.assertEqual(
            self.git(self.shared, "status", "--porcelain", "--untracked-files=all"), ""
        )
        self.write_module("used.json", {"name": "new"})
        (self.remote / "local.txt").write_text("remote replacement\n")
        self.commit(self.remote)

        checker = await self.checker()
        self.assertEqual(checker.update_manifests(), [])
        self.assertIn("would be overwritten", str(checker.get_errors()[0]))
        self.assertEqual(local.read_text(), "local content\n")
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)

    async def test_nonconflicting_ignored_file_does_not_block_update(self):
        excludes = self.directory / "ignore"
        excludes.write_text("local.txt\n")
        self.git(self.shared, "config", "core.excludesFile", str(excludes))
        local = self.shared / "local.txt"
        local.write_text("local content\n")
        self.write_module("used.json", {"name": "new"})
        candidate = self.commit(self.remote)

        checker = await self.checker()
        self.assertTrue(checker.update_manifests())
        self.assertEqual(checker.get_errors(), [])
        self.assertEqual(local.read_text(), "local content\n")
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), candidate)

    async def test_uninitialized_submodule(self):
        self.git(self.root, "submodule", "deinit", "--force", "--all")
        self.write_module("used.json", {"name": "new"})
        candidate = self.commit(self.remote)
        checker = await self.checker()
        self.assertFalse((self.shared / ".git").exists())
        self.assertTrue(checker.update_manifests())
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), candidate)

    async def test_filter_does_not_check_submodules(self):
        self.write_module("used.json", {"name": "new"})
        self.commit(self.remote)
        checker = ManifestChecker(str(self.manifest))
        await checker.check()
        self.assertEqual(len(checker.submodule_checker.updates), 1)
        await checker.check(ExternalData.Type.ARCHIVE)
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertEqual(checker.get_errors(), [])

    async def test_require_important_update_does_not_apply_submodule_alone(self):
        self.write_module("used.json", {"name": "new"})
        self.commit(self.remote)
        checker = await self.checker(require_important_update=True)
        self.assertEqual(len(checker.submodule_checker.updates), 1)
        self.assertEqual(checker.update_manifests(), [])

    async def test_configured_branch(self):
        self.git(self.remote, "checkout", "-b", "stable")
        self.write_module("used.json", {"name": "stable"})
        stable = self.commit(self.remote)
        self.git(self.remote, "checkout", "main")
        self.git(
            self.root,
            "config",
            "--file",
            ".gitmodules",
            "submodule.shared-modules.branch",
            "stable",
        )
        self.commit(self.root)
        checker = await self.checker()
        self.assertEqual(checker.submodule_checker.updates[0].candidate, stable)

    async def test_relative_url(self):
        self.git(
            self.root, "remote", "add", "origin", str(self.directory / "parent.git")
        )
        self.git(self.root, "config", "--unset", "submodule.shared-modules.url")
        self.git(
            self.root,
            "config",
            "--file",
            ".gitmodules",
            "submodule.shared-modules.url",
            "../remote",
        )
        self.commit(self.root)
        self.write_module("used.json", {"name": "new"})
        self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(len(checker.submodule_checker.updates), 1)

    async def test_cli_edit_only(self):
        self.write_module("used.json", {"name": "new"})
        candidate = self.commit(self.remote)
        args = main.parse_cli_args([str(self.manifest), "--edit-only"])
        self.assertEqual(await main.run_with_args(args), (1, 0, True))
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), candidate)

    async def test_source_manifest_include(self):
        self.write_module("sources.json", [{"type": "patch", "path": "patch.diff"}])
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.write_manifest({"name": "app", "sources": ["shared-modules/sources.json"]})
        self.commit(self.root)
        (self.remote / "patch.diff").write_text("new patch\n")
        self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(checker.submodule_checker.updates[0].files, ("patch.diff",))

    async def test_nested_submodule_is_not_initialized_or_updated(self):
        self.git(self.remote, "submodule", "add", str(self.root), "nested")
        self.write_module("used.json", {"name": "used", "modules": ["nested/app.json"]})
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.commit(self.root)
        self.write_module("unused.json", {"name": "unused"})
        self.commit(self.remote)
        with self.assertLogs("src.lib.submodules", level="INFO") as logs:
            checker = await self.checker()
        self.assertTrue(any("Skipping nested" in line for line in logs.output))
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertFalse((self.shared / "nested" / ".git").exists())

    async def test_git_worktree_and_subdirectory_manifest(self):
        worktree = self.directory / "worktree"
        self.git(self.root, "worktree", "add", "--detach", str(worktree))
        (worktree / "manifests").mkdir()
        self.manifest = worktree / "manifests" / "app.json"
        self.write_manifest({"name": "app", "modules": ["../shared-modules/used.json"]})
        self.write_module("used.json", {"name": "new"})
        candidate = self.commit(self.remote)
        checker = await self.checker()
        self.assertTrue(checker.update_manifests())
        self.assertEqual(
            self.git(worktree / "shared-modules", "rev-parse", "HEAD"), candidate
        )
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)

    async def test_cli_commit_only_records_gitlink(self):
        self.write_module("used.json", {"name": "new"})
        candidate = self.commit(self.remote)
        args = main.parse_cli_args([str(self.manifest), "--commit-only"])
        self.assertEqual(await main.run_with_args(args), (1, 0, True))
        self.assertIn(
            candidate, self.git(self.root, "ls-tree", "HEAD", "shared-modules")
        )
        self.assertEqual(self.git(self.root, "status", "--porcelain"), "")
        self.assertEqual(
            self.git(self.root, "log", "-1", "--format=%s"),
            f"Update submodule shared-modules to {candidate[:12]}",
        )

    async def test_report_includes_update_and_policy(self):
        self.write_module("used.json", {"name": "new"})
        candidate = self.commit(self.remote)
        destination = self.directory / "report.json"
        for policy in ([], ["--require-important-update"]):
            args = main.parse_cli_args(
                [
                    str(self.manifest),
                    "--report-json",
                    str(destination),
                    *policy,
                ]
            )
            self.assertEqual(await main.run_with_args(args), (1, 0, False))
            report = json.loads(destination.read_text())
            validate_report(report)
            self.assertEqual(
                report["submodules"],
                [
                    {
                        "path": "shared-modules",
                        "current": self.old,
                        "candidate": candidate,
                        "files": ["used.json"],
                        "status": "blocked" if policy else "update_available",
                    }
                ],
            )

    async def test_staged_gitlink_is_not_overwritten(self):
        self.write_module("used.json", {"name": "new"})
        self.commit(self.remote)
        checker = await self.checker()
        self.git(
            self.root,
            "update-index",
            "--cacheinfo",
            "160000",
            "a" * 40,
            "shared-modules",
        )
        self.assertEqual(checker.update_manifests(), [])
        self.assertIn("changed since checking", str(checker.get_errors()[0]))

    async def test_failed_fetch_is_reported(self):
        self.git(
            self.root,
            "config",
            "submodule.shared-modules.url",
            str(self.directory / "missing"),
        )
        checker = ManifestChecker(str(self.manifest))
        await checker.check()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertIn("Git clone failed", str(checker.get_errors()[0]))

    async def test_escaping_include_blocks_update(self):
        self.write_module("used.json", {"name": "used", "modules": ["../outside.json"]})
        self.commit(self.remote)
        checker = ManifestChecker(str(self.manifest))
        await checker.check()
        self.assertEqual(checker.submodule_checker.updates, [])
        self.assertIn("escapes submodule", str(checker.get_errors()[0]))

    async def test_submodule_path_with_spaces(self):
        self.git(self.root, "mv", "shared-modules", "shared modules")
        self.shared = self.root / "shared modules"
        self.write_manifest({"name": "app", "modules": ["shared modules/used.json"]})
        self.commit(self.root)
        self.write_module("used.json", {"name": "new"})
        candidate = self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(checker.submodule_checker.updates[0].path, "shared modules")
        self.assertTrue(checker.update_manifests())
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), candidate)

    async def test_check_outdated_exit_status(self):
        self.write_module("used.json", {"name": "new"})
        self.commit(self.remote)
        result = subprocess.run(
            [
                sys.executable,
                str(
                    Path(main.__file__).parent.parent / "flatpak-external-data-checker"
                ),
                "--check-outdated",
                str(self.manifest),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), self.old)

    async def test_symlink_source_tracks_target_changes(self):
        (self.remote / "patch-link").symlink_to("patch.diff")
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.write_manifest(
            {
                "name": "app",
                "sources": [
                    {
                        "type": "patch",
                        "path": "shared-modules/patch-link",
                    }
                ],
            }
        )
        self.commit(self.root)
        (self.remote / "patch.diff").write_text("new patch\n")
        self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(checker.submodule_checker.updates[0].files, ("patch.diff",))

    async def test_symlink_source_can_replace_its_old_target(self):
        (self.remote / "patch-link").symlink_to("patch.diff")
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.write_manifest(
            {
                "name": "app",
                "sources": [
                    {
                        "type": "patch",
                        "path": "shared-modules/patch-link",
                    }
                ],
            }
        )
        self.commit(self.root)
        (self.remote / "patch-link").unlink()
        (self.remote / "patch-link").symlink_to("new.diff")
        (self.remote / "new.diff").write_text("new patch\n")
        (self.remote / "patch.diff").unlink()
        candidate = self.commit(self.remote)

        (self.root / "alias").symlink_to("shared-modules", target_is_directory=True)
        for prefix in ("shared-modules", "alias"):
            with self.subTest(prefix=prefix):
                self.write_manifest(
                    {
                        "name": "app",
                        "sources": [
                            {
                                "type": "patch",
                                "path": f"{prefix}/patch-link",
                            }
                        ],
                    }
                )
                checker = await self.checker()
                self.assertEqual(
                    checker.submodule_checker.updates[0].files,
                    (
                        "new.diff",
                        "patch-link",
                        "patch.diff",
                    ),
                )
        self.assertTrue(checker.update_manifests())
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), candidate)

    async def test_directory_source_ignores_nested_gitlink_changes(self):
        self.git(self.remote, "submodule", "add", str(self.root), "assets/nested")
        (self.remote / "assets" / "file.txt").write_text("old\n")
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.commit(self.root)
        self.commit(self.remote / "assets" / "nested")
        self.commit(self.remote)

        for directory in ("shared-modules", "shared-modules/assets"):
            with self.subTest(directory=directory):
                self.write_manifest(
                    {
                        "name": "app",
                        "sources": [
                            {
                                "type": "dir",
                                "path": directory,
                            }
                        ],
                    }
                )
                checker = await self.checker()
                self.assertEqual(checker.submodule_checker.updates, [])

        (self.remote / "assets" / "file.txt").write_text("new\n")
        self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(
            checker.submodule_checker.updates[0].files, ("assets/file.txt",)
        )

    async def test_report_uses_v1_schema(self):
        destination = self.directory / "report.json"
        args = main.parse_cli_args(
            [
                str(self.manifest),
                "--report-json",
                str(destination),
            ]
        )
        self.assertEqual(await main.run_with_args(args), (0, 0, False))
        report = json.loads(destination.read_text())
        self.assertEqual(report["schema_version"], 1)
        validate_report(report)
        self.assertEqual(report["submodules"], [])

    async def test_source_through_symlinked_directory(self):
        (self.remote / "patches").mkdir()
        (self.remote / "patches" / "fix.patch").write_text("old\n")
        (self.remote / "alias").symlink_to("patches", target_is_directory=True)
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.write_manifest(
            {
                "name": "app",
                "sources": [
                    {
                        "type": "patch",
                        "path": "shared-modules/alias/fix.patch",
                    }
                ],
            }
        )
        self.commit(self.root)

        (self.remote / "patches" / "fix.patch").write_text("new\n")
        self.write_module("used.json", {"name": "also changed"})
        candidate = self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(
            checker.submodule_checker.updates[0].files, ("patches/fix.patch",)
        )
        self.assertTrue(checker.update_manifests())
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), candidate)

    async def test_symlinked_manifest_with_relative_sources(self):
        (self.remote / "manifests").mkdir()
        self.write_module(
            "manifests/used.json",
            {
                "name": "used",
                "sources": [{"type": "patch", "path": "patch.diff"}],
            },
        )
        (self.remote / "alias.json").symlink_to("manifests/used.json")
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.write_manifest({"name": "app", "modules": ["shared-modules/alias.json"]})
        self.commit(self.root)

        (self.remote / "patch.diff").write_text("new patch\n")
        self.write_module(
            "manifests/used.json",
            {
                "name": "updated",
                "sources": [{"type": "patch", "path": "patch.diff"}],
            },
        )
        candidate = self.commit(self.remote)
        checker = await self.checker()
        self.assertEqual(
            checker.submodule_checker.updates[0].files,
            (
                "manifests/used.json",
                "patch.diff",
            ),
        )
        self.assertTrue(checker.update_manifests())
        self.assertEqual(self.git(self.shared, "rev-parse", "HEAD"), candidate)

    async def test_symlink_escape_and_loop_block_updates(self):
        (self.remote / "patch-link").symlink_to("patch.diff")
        old = self.commit(self.remote)
        self.git(self.shared, "fetch", "origin")
        self.git(self.shared, "checkout", old)
        self.write_manifest(
            {
                "name": "app",
                "sources": [
                    {
                        "type": "patch",
                        "path": "shared-modules/patch-link",
                    }
                ],
            }
        )
        self.commit(self.root)

        for target, message in (
            ("../outside.patch", "escapes submodule"),
            ("patch-link", "Symlink loop"),
        ):
            with self.subTest(target=target):
                (self.remote / "patch-link").unlink()
                (self.remote / "patch-link").symlink_to(target)
                self.commit(self.remote)
                checker = ManifestChecker(str(self.manifest))
                await checker.check()
                self.assertEqual(checker.submodule_checker.updates, [])
                self.assertIn(message, str(checker.get_errors()[0]))
