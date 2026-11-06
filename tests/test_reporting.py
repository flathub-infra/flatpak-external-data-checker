import contextlib
import hashlib
import io
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from src import main

from .support import LocalHTTPTestCase


class TestReporting(LocalHTTPTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = self.root / "app.json"
        self.report = self.root / "report.json"

    def source(self, version="2", *, same=False):
        old_url = self.serve(b"old")
        new_url = old_url if same else self.serve(b"new")
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
        return result, json.loads(self.report.read_text())

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
            outputs.append((result, stdout.getvalue(), logs.output))
        self.assertEqual(outputs[0], outputs[1])
        self.assertIn("OUTDATED", outputs[1][1])
