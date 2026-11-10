import io
import json
import logging
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src import main
from src.lib.checksums import MultiDigest
from src.lib.errors import AppdataLoadError, CheckerFetchError, SourceLoadError
from src.lib.externaldata import ExternalData, ExternalFile
from src.lib.runtime import RuntimeStatus
from src.lib.sourceupdates import SourceUpdate

from .report_schema import validate_branch_report, validate_report

TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__), "net.invisible_island.xterm.yml"
)
TEST_APPDATA = os.path.join(
    os.path.dirname(__file__), "net.invisible_island.xterm.appdata.xml"
)


@patch.dict(os.environ)
class TestEntrypoint(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._clear_environment()

        # Exercise the real checkers/manifest/commit path, not changing upstreams.
        def get(url, **kwargs):
            version = {"5272": "365", "1766": "1.0.99"}[url.query["project_id"]]
            response = AsyncMock()
            response.json.return_value = {
                "latest_version": version,
                "stable_versions": [version],
            }
            response.__aenter__.return_value = response
            return response

        async def fetch(url, **kwargs):
            return ExternalFile(
                url=str(url),
                checksum=MultiDigest(sha256="a" * 64),
                size=123,
                version=None,
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )

        for patcher in (
            patch(
                "src.lib.runtime.fetch_runtime_statuses",
                return_value={
                    ("org.freedesktop.Sdk", version): [
                        RuntimeStatus(
                            f"runtime/org.freedesktop.Sdk/x86_64/{version}", None, None
                        )
                    ]
                    for version in ("24.08", "25.08")
                },
            ),
            patch("aiohttp.ClientSession.get", side_effect=get),
            patch("src.lib.utils.get_extra_data_info_from_url", side_effect=fetch),
            patch(
                "src.lib.utils.git_ls_remote",
                new_callable=AsyncMock,
                return_value={"refs/tags/xterm-365": "b" * 40},
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.test_dir = tempfile.TemporaryDirectory()
        self.manifest_filename = os.path.basename(TEST_MANIFEST)
        self.appdata_filename = os.path.basename(TEST_APPDATA)
        self.manifest_path = os.path.join(self.test_dir.name, self.manifest_filename)
        self.appdata_path = os.path.join(self.test_dir.name, self.appdata_filename)
        self._run_cmd(["git", "init"])
        self._run_cmd(["git", "config", "user.name", "Test Runner"])
        self._run_cmd(["git", "config", "user.email", "test@localhost"])
        shutil.copy(TEST_MANIFEST, self.manifest_path)
        shutil.copy(TEST_APPDATA, self.appdata_path)
        self._run_cmd(["git", "add", self.manifest_filename])
        self._run_cmd(["git", "add", self.appdata_filename])
        self._run_cmd(["git", "commit", "-a", "-m", "Initial commit"])

    def tearDown(self):
        self.test_dir.cleanup()

    def _clear_environment(self):
        unwanted_vars = [
            "EMAIL",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ]
        for var in unwanted_vars:
            os.environ.pop(var, None)

    def _run_cmd(self, cmd, **kwargs):
        return subprocess.run(cmd, cwd=self.test_dir.name, check=True, **kwargs)

    def _get_commit_data(self, rev="HEAD"):
        data = {}
        for name, fmt in [
            ("commit", "%H"),
            ("subject", "%s"),
            ("body", "%b"),
            ("author_name", "%an"),
            ("author_email", "%ae"),
            ("committer_name", "%cn"),
            ("committer_email", "%ce"),
        ]:
            cmd = ["git", "show", "--no-patch", f"--pretty=format:{fmt}", rev]
            proc = self._run_cmd(cmd, stdout=subprocess.PIPE)
            output = proc.stdout.decode("utf-8")
            data[name] = output

        return data

    async def test_full_run(self):
        args1 = main.parse_cli_args(["--update", "--commit-only", self.manifest_path])
        self.assertEqual(await main.run_with_args(args1), (2, 0, True))

        commit_data = self._get_commit_data()
        self.assertEqual(commit_data["subject"], "Update libXaw and xterm modules")
        self.assertEqual(commit_data["author_name"], "Test Runner")
        self.assertEqual(commit_data["author_email"], "test@localhost")
        self.assertEqual(commit_data["committer_name"], "Test Runner")
        self.assertEqual(commit_data["committer_email"], "test@localhost")

        body_lines = commit_data["body"].splitlines()
        self.assertEqual(len(body_lines), 2)
        self.assertRegex(body_lines[0], r"^libXaw: Update libXaw-1.0.12.tar.bz2 to ")
        self.assertRegex(body_lines[1], r"^xterm: Update xterm-snapshots.git to ")

        args2 = main.parse_cli_args([self.manifest_path])
        self.assertEqual(await main.run_with_args(args2), (0, 0, False))

    async def test_eol_apps(self):
        config = Path(self.test_dir.name, "flathub.json")
        report_path = Path(self.test_dir.name, "report.json")
        for key in ("end-of-life", "end-of-life-rebase"):
            for option in (
                None,
                "--update",
                "--commit-only",
                "--edit-only",
                "--commit-to-current-branch",
            ):
                with self.subTest(key=key, option=option):
                    config.write_text(json.dumps({key: "Retired"}))
                    args = main.parse_cli_args(
                        ([option] if option else []) + [self.manifest_path]
                    )
                    args.report_json = str(report_path)
                    with patch("src.main.manifest.ManifestChecker") as checker:
                        self.assertEqual(await main.run_with_args(args), (0, 0, False))
                        checker.return_value.check.assert_not_called()
                    report = json.loads(report_path.read_text())
                    validate_report(report)
                    self.assertEqual(report["status"], "skipped")
                    self.assertEqual(report["delivery"], "not_requested")
                    self.assertEqual(report["sources"], [])
                    self.assertEqual(report["errors"], [])
        args = main.parse_cli_args(["--lint", self.manifest_path])
        with patch("src.main.ManifestLinter") as linter:
            linter.return_value.lint.return_value = []
            self.assertEqual(await main.run_with_args(args), (0, 0, False))
            linter.return_value.lint.assert_called_once()

    async def test_invalid_eol_metadata(self):
        for config in ("{", "[]"):
            with self.subTest(config=config):
                Path(self.test_dir.name, "flathub.json").write_text(config)
                args = main.parse_cli_args([self.manifest_path])
                with patch("src.main.manifest.ManifestChecker") as checker:
                    self.assertEqual(await main.run_with_args(args), (0, 1, False))
                    checker.assert_not_called()

    async def test_eol_app_skips_invalid_manifest(self):
        Path(self.test_dir.name, "flathub.json").write_text(
            '{"end-of-life": "Retired"}'
        )
        for contents in ("{}", "["):
            with self.subTest(contents=contents):
                Path(self.manifest_path).write_text(contents)
                args = main.parse_cli_args([self.manifest_path])
                self.assertEqual(await main.run_with_args(args), (0, 0, False))

    def test_eol_metadata_location_and_values(self):
        config = Path(self.test_dir.name, "flathub.json")
        nested_manifest = Path(self.test_dir.name, "nested", "app.yml")
        for value, expected in (
            (None, False),
            (False, False),
            ("", True),
            ("Retired", True),
        ):
            with self.subTest(value=value):
                config.write_text(json.dumps({"end-of-life": value}))
                self.assertEqual(main.is_end_of_life(nested_manifest), expected)
        config.unlink()
        self.assertFalse(main.is_end_of_life(nested_manifest))
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "flathub.json").write_text('{"end-of-life": "Retired"}')
            self.assertTrue(main.is_end_of_life(Path(directory, "app.yml")))

    async def test_runtime_branches(self):
        self._make_extension()
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(["git", "init", "--bare", remote], check=True)
            self._run_cmd(["git", "remote", "add", "origin", remote])
            original_head = self._get_commit_data()["commit"]
            for runtime in ("24.08", "25.08"):
                self._run_cmd(
                    ["git", "checkout", "-B", f"branch/{runtime}", original_head]
                )
                Path(self.test_dir.name, "runtime.txt").write_text(runtime)
                manifest_file = Path(self.manifest_path)
                manifest_file.write_text(
                    manifest_file.read_text().replace(
                        "runtime-version: '24.08'", f"runtime-version: '{runtime}'"
                    )
                )
                self._run_cmd(["git", "add", "runtime.txt", self.manifest_filename])
                self._run_cmd(["git", "commit", "-m", f"Runtime {runtime}"])
                self._run_cmd(["git", "push", "origin", f"branch/{runtime}"])
            self._run_cmd(["git", "checkout", "--detach", original_head])
            local_manifest = (
                Path(self.manifest_path).read_text() + "# local uncommitted work\n"
            )
            Path(self.manifest_path).write_text(local_manifest)
            args = main.parse_cli_args(["--commit-only", self.manifest_path])
            report_path = Path(self.test_dir.name, "report.json")
            args.report_json = str(report_path)
            self.assertEqual(await main.run_with_args(args), (4, 0, True))
            report = json.loads(report_path.read_text())
            validate_report(report)
            self.assertEqual(report["status"], "completed")
            self.assertEqual(report["delivery"], "committed")
            self.assertEqual(report["errors"], [])
            self.assertEqual(len(report["sources"]), 4)
            self.assertEqual(
                [entry["branch"] for entry in report["sources"]],
                ["branch/24.08"] * 2 + ["branch/25.08"] * 2,
            )
            self.assertEqual(len(report["branches"]), 2)
            self.assertEqual(self._get_commit_data()["commit"], original_head)
            self.assertEqual(Path(self.manifest_path).read_text(), local_manifest)
            branches = self._run_cmd(
                ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            updates = [branch for branch in branches if branch.startswith("update-")]
            self.assertEqual(len(updates), 2)
            for runtime in ("24.08", "25.08"):
                branch = next(
                    b for b in updates if b.startswith(f"update-branch/{runtime}-")
                )
                result = self._run_cmd(
                    ["git", "show", f"{branch}:runtime.txt"],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.stdout, runtime)
                result = self._run_cmd(
                    ["git", "show", f"{branch}:{self.manifest_filename}"],
                    capture_output=True,
                    text=True,
                )
                self.assertIn(f"runtime-version: '{runtime}'", result.stdout)
                self.assertEqual(
                    self._get_commit_data(branch)["subject"],
                    "Update libXaw and xterm modules",
                )
            worktrees = self._run_cmd(
                ["git", "worktree", "list", "--porcelain"],
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(worktrees.count("worktree "), 1)

            args.commit_only = False
            args.update = True
            with patch("src.main.open_pr", return_value=True) as open_pr:
                self.assertEqual(await main.run_with_args(args), (4, 0, True))
                self.assertEqual(
                    [call.args[0].base_branch for call in open_pr.call_args_list],
                    ["branch/24.08", "branch/25.08"],
                )

            # A branch error must not hide other branches' outstanding updates.
            self._run_cmd(["git", "push", "origin", "HEAD:refs/heads/branch/00.00"])
            args.update = False
            with patch(
                "src.main.run_manifest_with_args", new_callable=AsyncMock
            ) as run:
                run.side_effect = [(0, 1, False), (2, 0, True), (1, 0, False)]
                report = {"sources": [], "errors": [], "delivery": "not_requested"}
                self.assertEqual(
                    await main.run_runtime_branches(args, report), (3, 1, False)
                )
                self.assertEqual(run.await_count, 3)

            self._run_cmd(
                [
                    "git",
                    "push",
                    "origin",
                    "--delete",
                    "branch/00.00",
                    "branch/24.08",
                    "branch/25.08",
                ]
            )
            self.assertEqual(await main.run_with_args(args), (2, 0, False))

    async def test_runtime_branch_manifest_formats(self):
        self._run_cmd(
            ["git", "mv", self.manifest_filename, "net.invisible_island.xterm.yaml"]
        )
        self.manifest_filename = "net.invisible_island.xterm.yaml"
        self.manifest_path = str(Path(self.test_dir.name, self.manifest_filename))
        self._make_extension()
        original_head = self._get_commit_data()["commit"]
        original_manifest = Path(self.manifest_path).read_text()
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(["git", "init", "--bare", remote], check=True)
            self._run_cmd(["git", "remote", "add", "origin", remote])
            for runtime, suffix in (("24.08", ".json"), ("25.08", ".yml")):
                self._run_cmd(
                    ["git", "checkout", "-B", f"branch/{runtime}", original_head]
                )
                path = Path(self.manifest_path)
                contents = main.read_manifest(path)[0]
                contents["runtime-version"] = runtime
                contents["modules"][0]["modules"].pop()
                path.unlink()
                path.with_suffix(suffix).write_text(json.dumps(contents))
                self._run_cmd(["git", "add", "-A"])
                self._run_cmd(["git", "commit", "-m", "Change manifest format"])
                self._run_cmd(["git", "push", "origin", f"branch/{runtime}"])
            self._run_cmd(["git", "checkout", "--detach", original_head])
            local_manifest = original_manifest + "# local uncommitted work\n"
            Path(self.manifest_path).write_text(local_manifest)
            args = main.parse_cli_args(["--commit-only", self.manifest_path])
            report_path = Path(self.test_dir.name, "report.json")
            args.report_json = str(report_path)
            self.assertEqual(await main.run_with_args(args), (4, 0, True))
            report = json.loads(report_path.read_text())
            validate_report(report)
            self.assertEqual(report["errors"], [])
            self.assertEqual(
                [entry["status"] for entry in report["branches"]],
                ["completed", "completed"],
            )
            branches = self._run_cmd(
                ["git", "for-each-ref", "--format=%(refname:short)", "refs/heads/"],
                capture_output=True,
                text=True,
            ).stdout.splitlines()
            updates = [branch for branch in branches if branch.startswith("update-")]
            self.assertEqual(len(updates), 2)
            for runtime, suffix in (("24.08", ".json"), ("25.08", ".yml")):
                branch = next(
                    b for b in updates if b.startswith(f"update-branch/{runtime}-")
                )
                result = self._run_cmd(
                    [
                        "git",
                        "show",
                        f"{branch}:{Path(self.manifest_filename).with_suffix(suffix)}",
                    ],
                    capture_output=True,
                    text=True,
                )
                self.assertIn(runtime, result.stdout)
                self.assertIn("1.0.99", result.stdout)
            self.assertEqual(self._get_commit_data()["commit"], original_head)
            self.assertEqual(Path(self.manifest_path).read_text(), local_manifest)
            worktrees = self._run_cmd(
                ["git", "worktree", "list", "--porcelain"],
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(worktrees.count("worktree "), 1)

    async def test_runtime_branch_manifest_selection_errors(self):
        self._run_cmd(
            ["git", "mv", self.manifest_filename, "net.invisible_island.xterm.yaml"]
        )
        self.manifest_filename = "net.invisible_island.xterm.yaml"
        self.manifest_path = str(Path(self.test_dir.name, self.manifest_filename))
        self._make_extension()
        original_head = self._get_commit_data()["commit"]
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(["git", "init", "--bare", remote], check=True)
            self._run_cmd(["git", "remote", "add", "origin", remote])
            for runtime in ("01-eol", "02-missing", "03-ambiguous", "04-explicit"):
                self._run_cmd(
                    ["git", "checkout", "-B", f"branch/{runtime}", original_head]
                )
                path = Path(self.manifest_path)
                if runtime != "04-explicit":
                    path.unlink()
                if runtime in ("03-ambiguous", "04-explicit"):
                    for suffix in (".json", ".yml"):
                        path.with_suffix(suffix).write_text("{}")
                Path(self.test_dir.name, "unrelated.yaml").write_text("{}")
                if runtime == "01-eol":
                    Path(self.test_dir.name, "flathub.json").write_text(
                        '{"end-of-life": "Retired"}'
                    )
                self._run_cmd(["git", "add", "-A"])
                self._run_cmd(["git", "commit", "-m", "Set branch manifests"])
                self._run_cmd(["git", "push", "origin", f"branch/{runtime}"])
            self._run_cmd(["git", "checkout", "--detach", original_head])
            args = main.parse_cli_args([self.manifest_path])
            report = {"sources": [], "errors": [], "delivery": "not_requested"}
            with patch(
                "src.main.run_manifest_with_args", return_value=(2, 0, False)
            ) as run:
                self.assertEqual(
                    await main.run_runtime_branches(args, report), (2, 2, False)
                )
                run.assert_awaited_once()
                self.assertEqual(
                    run.await_args.kwargs["base_branch"], "branch/04-explicit"
                )
                self.assertEqual(
                    Path(run.await_args.args[0].manifest).name, self.manifest_filename
                )
            self.assertEqual(
                [entry["status"] for entry in report["branches"]],
                ["skipped", "failed", "failed", "completed"],
            )
            self.assertEqual(
                [error["type"] for error in report["errors"]],
                ["FileNotFoundError", "ValueError"],
            )

    def _make_extension(self):
        path = Path(self.manifest_path)
        path.write_text(
            "build-extension: true\nruntime: org.freedesktop.Sdk\n"
            "runtime-version: '24.08'\n" + path.read_text()
        )
        self._run_cmd(["git", "commit", "-am", "Build extension"])

    async def test_runtime_branch_eol(self):
        self._make_extension()
        original_head = self._get_commit_data()["commit"]
        configs = {
            "01-eol": '{"end-of-life": "Retired"}',
            "02-rebase": '{"end-of-life-rebase": "org.example.New"}',
            "03-empty-reason": '{"end-of-life": ""}',
            "04-invalid": "{",
            "05-invalid-object": "[]",
            "06-active": '{"end-of-life": null, "end-of-life-rebase": false}',
            "07-missing": None,
        }
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(["git", "init", "--bare", remote], check=True)
            self._run_cmd(["git", "remote", "add", "origin", remote])
            for runtime, config in configs.items():
                self._run_cmd(
                    ["git", "checkout", "-B", f"branch/{runtime}", original_head]
                )
                if config is not None:
                    Path(self.test_dir.name, "flathub.json").write_text(config)
                    self._run_cmd(["git", "add", "flathub.json"])
                    self._run_cmd(["git", "commit", "-m", "Set branch metadata"])
                self._run_cmd(["git", "push", "origin", f"branch/{runtime}"])
            self._run_cmd(["git", "checkout", "--detach", original_head])
            args = main.parse_cli_args(["--update", self.manifest_path])
            report = {"sources": [], "errors": [], "delivery": "not_requested"}
            with (
                patch(
                    "src.main.run_manifest_with_args", return_value=(2, 0, True)
                ) as run,
                patch("src.main.check_call", wraps=main.check_call) as check_call,
            ):
                self.assertEqual(
                    await main.run_runtime_branches(args, report), (4, 2, False)
                )
                self.assertEqual(
                    [call.kwargs["base_branch"] for call in run.await_args_list],
                    ["branch/06-active", "branch/07-missing"],
                )
                self.assertEqual(
                    sum(
                        call.args[0][:2] == ["git", "submodule"]
                        for call in check_call.call_args_list
                    ),
                    0,
                )
            self.assertEqual(
                [entry["status"] for entry in report["branches"]],
                ["skipped"] * 3 + ["failed"] * 2 + ["completed"] * 2,
            )
            for entry in report["branches"][:3]:
                self.assertEqual(entry["delivery"], "not_requested")
                self.assertEqual(entry["sources"], [])
                self.assertEqual(entry["errors"], [])
            self.assertEqual(
                [error["type"] for error in report["errors"]],
                ["JSONDecodeError", "ValueError"],
            )
            self._run_cmd(
                ["git", "push", "origin", "--delete"]
                + [f"branch/{runtime}" for runtime in list(configs)[3:]]
            )
            report = {"sources": [], "errors": [], "delivery": "not_requested"}
            with patch("src.main.run_manifest_with_args") as run:
                self.assertEqual(
                    await main.run_runtime_branches(args, report), (0, 0, False)
                )
                run.assert_not_called()
            self.assertEqual(
                [entry["status"] for entry in report["branches"]], ["skipped"] * 3
            )
            self.assertEqual(report["status"], "skipped")
            worktrees = self._run_cmd(
                ["git", "worktree", "list", "--porcelain"],
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(worktrees.count("worktree "), 1)
            self.assertEqual(self._get_commit_data()["commit"], original_head)

    async def test_runtime_branch_report_aggregation(self):
        self._make_extension()
        original_head = self._get_commit_data()["commit"]
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(["git", "init", "--bare", remote], check=True)
            self._run_cmd(["git", "remote", "add", "origin", remote])
            for version in ("24.08", "25.08"):
                self._run_cmd(
                    ["git", "checkout", "-B", f"branch/{version}", original_head]
                )
                path = Path(self.manifest_path)
                path.write_text(path.read_text().replace("'24.08'", f"'{version}'"))
                self._run_cmd(["git", "commit", "--allow-empty", "-am", "Set runtime"])
                self._run_cmd(["git", "push", "origin", f"branch/{version}"])
            self._run_cmd(["git", "checkout", "--detach", original_head])
            report_path = Path(self.test_dir.name, "report.json")
            args = main.parse_cli_args(
                ["--report-json", str(report_path), self.manifest_path]
            )
            for first in ("skipped", "completed", "failed"):
                for second in ("skipped", "completed", "failed"):
                    statuses = [first, second]
                    parents = {
                        ("org.freedesktop.Sdk", version): [
                            RuntimeStatus(
                                f"runtime/org.freedesktop.Sdk/x86_64/{version}",
                                "Retired SDK" if status == "skipped" else None,
                                None,
                            )
                        ]
                        for version, status in zip(
                            ("24.08", "25.08"), statuses, strict=True
                        )
                        if status != "failed"
                    }
                    with (
                        self.subTest(statuses=statuses),
                        patch(
                            "src.lib.runtime.fetch_runtime_statuses",
                            return_value=parents,
                        ),
                    ):
                        self.assertEqual(
                            await main.run_with_args(args),
                            (
                                2 * statuses.count("completed"),
                                statuses.count("failed"),
                                False,
                            ),
                        )
                        report = json.loads(report_path.read_text())
                        validate_report(report)
                        expected = (
                            "failed"
                            if "failed" in statuses
                            else "completed"
                            if "completed" in statuses
                            else "skipped"
                        )
                        self.assertEqual(report["status"], expected)
                        self.assertEqual(
                            [entry["status"] for entry in report["branches"]], statuses
                        )
                        self.assertEqual(report["schema_version"], 1)
                        self.assertEqual(report["delivery"], "not_requested")
                        if "completed" not in statuses:
                            self.assertEqual(report["sources"], [])
            self.assertEqual(self._get_commit_data()["commit"], original_head)

    async def test_runtime_branch_parent_eol(self):
        self._make_extension()
        original_head = self._get_commit_data()["commit"]
        parent = RuntimeStatus(
            "runtime/org.freedesktop.Sdk/x86_64/23.08", "Retired SDK", None
        )
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(["git", "init", "--bare", remote], check=True)
            self._run_cmd(["git", "remote", "add", "origin", remote])
            for branch, version in (
                ("01-retired", "23.08"),
                ("02-unknown", "missing"),
                ("03-active", "24.08"),
                ("04-retired", "23.08"),
            ):
                self._run_cmd(
                    ["git", "checkout", "-B", f"branch/{branch}", original_head]
                )
                path = Path(self.manifest_path)
                path.write_text(path.read_text().replace("'24.08'", f"'{version}'"))
                self._run_cmd(["git", "commit", "--allow-empty", "-am", "Set runtime"])
                self._run_cmd(["git", "push", "origin", f"branch/{branch}"])
            self._run_cmd(["git", "checkout", "--detach", original_head])
            args = main.parse_cli_args(["--update", self.manifest_path])
            report = {"sources": [], "errors": [], "delivery": "not_requested"}
            with (
                patch(
                    "src.lib.runtime.fetch_runtime_statuses",
                    return_value={
                        ("org.freedesktop.Sdk", "23.08"): [parent],
                        ("org.freedesktop.Sdk", "24.08"): [
                            RuntimeStatus(
                                "runtime/org.freedesktop.Sdk/x86_64/24.08", None, None
                            )
                        ],
                    },
                ) as fetch,
                patch(
                    "src.main.run_manifest_with_args", return_value=(2, 0, True)
                ) as run,
                self.assertLogs("src.main", level="INFO") as logs,
            ):
                self.assertEqual(
                    await main.run_runtime_branches(args, report), (2, 1, False)
                )
                fetch.assert_called_once()
                run.assert_awaited_once()
                self.assertEqual(
                    run.await_args.kwargs["base_branch"], "branch/03-active"
                )
            self.assertIn("Retired SDK", "\n".join(logs.output))
            report = json.loads(json.dumps(report))
            for entry in report["branches"]:
                validate_branch_report(entry)
            self.assertEqual(
                [entry["status"] for entry in report["branches"]],
                ["skipped", "failed", "completed", "skipped"],
            )
            for index in (0, 3):
                entry = report["branches"][index]
                self.assertEqual(entry["skip_reason"], "runtime-end-of-life")
                self.assertEqual(entry["runtime_eol"][0]["ref"], parent.ref)
                self.assertEqual(entry["runtime_eol"][0]["end_of_life"], "Retired SDK")
                self.assertEqual(entry["sources"], [])
                self.assertEqual(entry["delivery"], "not_requested")
            self.assertIn("not found on Flathub", report["errors"][0]["message"])
            worktrees = self._run_cmd(
                ["git", "worktree", "list", "--porcelain"],
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(worktrees.count("worktree "), 1)
            self.assertEqual(self._get_commit_data()["commit"], original_head)

    async def test_extension_current_checkout_modes(self):
        self._make_extension()
        for option in ("--edit-only", "--commit-to-current-branch", "--lint"):
            with (
                self.subTest(option=option),
                patch(
                    "src.main.run_runtime_branches", new_callable=AsyncMock
                ) as discover,
            ):
                args = main.parse_cli_args([option, self.manifest_path])
                await main.run_with_args(args)
                discover.assert_not_awaited()

    async def test_eol_checkout_still_discovers_runtime_branches(self):
        self._make_extension()
        path = Path(self.manifest_path)
        path.write_text(
            path.read_text().replace(
                "x-checker-data:", "x-checker-data:\n          update-group: app", 1
            )
            + "\n  - shared-modules/missing.yml\n"
        )
        Path(self.test_dir.name, "flathub.json").write_text(
            '{"end-of-life": "Retired"}'
        )
        args = main.parse_cli_args([self.manifest_path])
        with patch("src.main.run_runtime_branches", return_value=(2, 0, True)) as run:
            self.assertEqual(await main.run_with_args(args), (2, 0, True))
            run.assert_awaited_once()

    async def test_invalid_checkout_metadata_does_not_block_other_branches(self):
        self._make_extension()
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(["git", "init", "--bare", remote], check=True)
            self._run_cmd(["git", "remote", "add", "origin", remote])
            self._run_cmd(["git", "push", "origin", "HEAD:refs/heads/branch/25.08"])
            Path(self.test_dir.name, "flathub.json").write_text("{")
            self._run_cmd(["git", "add", "flathub.json"])
            self._run_cmd(["git", "commit", "-m", "Invalid metadata"])
            self._run_cmd(["git", "push", "origin", "HEAD:refs/heads/branch/24.08"])
            report_path = Path(self.test_dir.name, "report.json")
            args = main.parse_cli_args(
                ["--report-json", str(report_path), self.manifest_path]
            )
            self.assertEqual(await main.run_with_args(args), (2, 1, False))
            report = json.loads(report_path.read_text())
            validate_report(report)
            self.assertEqual(
                [branch["status"] for branch in report["branches"]],
                ["failed", "completed"],
            )
            self.assertEqual(report["errors"][0]["branch"], "branch/24.08")

    async def test_extension_without_origin(self):
        self._make_extension()
        args = main.parse_cli_args([self.manifest_path])
        self.assertEqual(await main.run_with_args(args), (2, 0, False))

    async def test_normal_manifest_does_not_discover_branches(self):
        with patch("src.main.run_runtime_branches", new_callable=AsyncMock) as discover:
            args = main.parse_cli_args([self.manifest_path])
            self.assertEqual(await main.run_with_args(args), (2, 0, False))
            discover.assert_not_awaited()

    async def test_runtime_branch_delivery_failure_continues(self):
        self._make_extension()
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(["git", "init", "--bare", remote], check=True)
            self._run_cmd(["git", "remote", "add", "origin", remote])
            for runtime in ("24.08", "25.08"):
                self._run_cmd(
                    ["git", "push", "origin", f"HEAD:refs/heads/branch/{runtime}"]
                )
            args = main.parse_cli_args(["--update", self.manifest_path])
            report_path = Path(self.test_dir.name, "report.json")
            args.report_json = str(report_path)
            for failure, error_type in (
                (RuntimeError("Failed to merge PR: merge rejected"), "RuntimeError"),
                (
                    json.JSONDecodeError("Invalid configuration", "{", 1),
                    "JSONDecodeError",
                ),
                (False, "PublicationError"),
            ):
                for reporting in (True, False):
                    args.report_json = str(report_path) if reporting else None
                    with (
                        self.subTest(failure=error_type, reporting=reporting),
                        patch(
                            "src.main.open_pr", side_effect=[failure, True]
                        ) as open_pr,
                    ):
                        self.assertEqual(await main.run_with_args(args), (4, 1, False))
                        self.assertEqual(
                            [
                                call.args[0].base_branch
                                for call in open_pr.call_args_list
                            ],
                            ["branch/24.08", "branch/25.08"],
                        )
                        if not reporting:
                            continue
                        report = json.loads(report_path.read_text())
                        validate_report(report)
                        self.assertEqual(report["status"], "failed")
                        self.assertEqual(report["delivery"], "failed")
                        self.assertEqual(len(report["sources"]), 4)
                        self.assertEqual(report["errors"][0]["branch"], "branch/24.08")
                        self.assertEqual(report["errors"][0]["type"], error_type)
                        self.assertEqual(
                            [entry["outdated"] for entry in report["branches"]], [2, 2]
                        )
                        self.assertEqual(
                            [entry["delivery"] for entry in report["branches"]],
                            ["failed", "pr_handled"],
                        )
                        self.assertEqual(
                            [entry["status"] for entry in report["branches"]],
                            ["failed", "completed"],
                        )
            worktrees = self._run_cmd(
                ["git", "worktree", "list", "--porcelain"],
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(worktrees.count("worktree "), 1)

    async def test_runtime_branch_submodule(self):
        with (
            tempfile.TemporaryDirectory() as remote,
            tempfile.TemporaryDirectory() as shared,
        ):
            subprocess.run(["git", "init", "--bare", remote], check=True)
            subprocess.run(["git", "init", shared], check=True)
            Path(shared, "module.yml").write_text(
                "name: shared\n" + Path(TEST_MANIFEST).read_text()
            )
            subprocess.run(["git", "-C", shared, "add", "."], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    shared,
                    "-c",
                    "user.name=Test Runner",
                    "-c",
                    "user.email=test@localhost",
                    "commit",
                    "-m",
                    "Shared module",
                ],
                check=True,
            )
            # Local submodule remotes keep this regression test offline.
            with patch.dict(os.environ, {"GIT_ALLOW_PROTOCOL": "file"}):
                self._run_cmd(["git", "submodule", "add", shared, "shared-modules"])
                Path(self.manifest_path).write_text(
                    "id: net.invisible_island.xterm\nbuild-extension: true\n"
                    "runtime: org.freedesktop.Sdk\nruntime-version: '24.08'\nmodules:\n"
                    "  - shared-modules/module.yml\n"
                )
                self._run_cmd(["git", "commit", "-am", "Use shared module"])
                self._run_cmd(["git", "remote", "add", "origin", remote])
                self._run_cmd(["git", "push", "origin", "HEAD:refs/heads/branch/25.08"])
                args = main.parse_cli_args([self.manifest_path])
                with patch("src.main.check_call", wraps=main.check_call) as git_call:
                    self.assertEqual(await main.run_with_args(args), (0, 0, False))
                self.assertFalse(
                    any("submodule" in call.args[0] for call in git_call.call_args_list)
                )
                shared_path = Path(self.test_dir.name, "shared-modules/module.yml")
                original_shared = shared_path.read_text()
                for option in ("--edit-only", "--commit-only"):
                    with self.subTest(option=option):
                        args = main.parse_cli_args([option, self.manifest_path])
                        self.assertEqual(await main.run_with_args(args), (0, 0, False))
                        self.assertEqual(shared_path.read_text(), original_shared)
                Path(self.test_dir.name, "local.yml").write_text(
                    "name: local\n" + Path(TEST_MANIFEST).read_text()
                )
                with Path(self.manifest_path).open("a") as manifest_file:
                    manifest_file.write("  - local.yml\n")
                self._run_cmd(["git", "add", "local.yml", self.manifest_filename])
                self._run_cmd(["git", "commit", "-m", "Add local module"])
                self._run_cmd(["git", "push", "origin", "HEAD:refs/heads/branch/25.08"])
                report_path = Path(self.test_dir.name, "report.json")
                args = main.parse_cli_args(
                    [
                        "--commit-only",
                        "--report-json",
                        str(report_path),
                        self.manifest_path,
                    ]
                )
                self.assertEqual(await main.run_with_args(args), (2, 0, True))
                report = json.loads(report_path.read_text())
                validate_report(report)
                self.assertEqual(report["status"], "failed")
                self.assertEqual(
                    [error["type"] for error in report["errors"]],
                    ["ManifestScopeError"],
                )
                self.assertEqual(len(report["sources"]), 2)
                self.assertTrue(
                    all(
                        source["manifest"] == "local.yml"
                        for source in report["sources"]
                    )
                )
                report_path.unlink()
                updates = self._run_cmd(
                    [
                        "git",
                        "for-each-ref",
                        "--format=%(refname)",
                        "refs/heads/",
                    ],
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
                updates = [
                    ref for ref in updates if ref.startswith("refs/heads/update-")
                ]
                self.assertEqual(len(updates), 1)
                changed = self._run_cmd(
                    [
                        "git",
                        "diff-tree",
                        "--no-commit-id",
                        "--name-only",
                        "-r",
                        updates[0],
                    ],
                    capture_output=True,
                    text=True,
                ).stdout.splitlines()
                self.assertEqual(changed, ["local.yml", self.appdata_filename])
                self.assertNotIn("shared", self._get_commit_data(updates[0])["subject"])
                args = main.parse_cli_args(
                    ["--commit-only", "--commit-to-current-branch", self.manifest_path]
                )
                self.assertEqual(await main.run_with_args(args), (2, 0, True))
                self.assertEqual(shared_path.read_text(), original_shared)
                self.assertEqual(
                    self._run_cmd(
                        ["git", "status", "--porcelain"],
                        capture_output=True,
                        text=True,
                    ).stdout,
                    "",
                )
            self.assertTrue(
                Path(self.test_dir.name, "shared-modules/module.yml").is_file()
            )
            worktrees = self._run_cmd(
                ["git", "worktree", "list", "--porcelain"],
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(worktrees.count("worktree "), 1)

    async def test_git_envvars(self):
        os.environ["GIT_AUTHOR_NAME"] = "Some Guy"
        os.environ["GIT_AUTHOR_EMAIL"] = "someguy@localhost"
        args1 = main.parse_cli_args(["--update", "--commit-only", self.manifest_path])
        self.assertEqual(await main.run_with_args(args1), (2, 0, True))

        commit_data = self._get_commit_data()
        self.assertEqual(commit_data["subject"], "Update libXaw and xterm modules")
        self.assertEqual(commit_data["author_name"], "Some Guy")
        self.assertEqual(commit_data["author_email"], "someguy@localhost")
        self.assertEqual(commit_data["committer_name"], "Test Runner")
        self.assertEqual(commit_data["committer_email"], "test@localhost")

    async def test_branch_exists(self):
        current_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=self.test_dir.name, text=True
        ).strip()
        with main.indir(self.test_dir.name):
            self.assertTrue(main.branch_exists(current_branch))
            self.assertFalse(main.branch_exists("nonexistent-branch"))

    async def test_commit_to_base_branch(self):
        base_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=self.test_dir.name, text=True
        ).strip()

        args = main.parse_cli_args(
            [
                "--update",
                "--commit-only",
                "--commit-to-current-branch",
                self.manifest_path,
            ]
        )

        with patch("src.main.open_pr") as mock_open_pr:
            result = await main.run_with_args(args)
            self.assertEqual(result, (2, 0, True))
            mock_open_pr.assert_not_called()

        branch_name = self._run_cmd(
            ["git", "branch", "--show-current"], stdout=subprocess.PIPE
        )
        self.assertEqual(branch_name.stdout.decode().strip(), base_branch)

        commit_msg_proc = self._run_cmd(
            ["git", "log", "-1", "--pretty=%s"], stdout=subprocess.PIPE
        )
        commit_msg = commit_msg_proc.stdout.decode().strip()
        self.assertEqual(commit_msg, "Update libXaw and xterm modules")

    async def test_commit_to_base_no_base_branch(self):
        self._run_cmd(["git", "checkout", "--detach"])

        args = main.parse_cli_args(
            [
                "--update",
                "--commit-only",
                "--commit-to-current-branch",
                self.manifest_path,
            ]
        )

        with patch("src.main.open_pr") as mock_open_pr:
            result = await main.run_with_args(args)
            self.assertEqual(result, (-1, -1, False))
            mock_open_pr.assert_not_called()


class TestCommitFallback(unittest.TestCase):
    def setUp(self):
        self._clear_environment()
        self.test_dir = tempfile.TemporaryDirectory()
        self.manifest_filename = "test.yaml"
        self.manifest_path = os.path.join(self.test_dir.name, self.manifest_filename)

        with open(self.manifest_path, "w") as f:
            f.write("test: content\n")

        self._run(["git", "init"])
        self._run(["git", "add", self.manifest_filename])
        self._run(
            [
                "git",
                "-c",
                "user.name=testuser",
                "-c",
                "user.email=testuser@localhost",
                "commit",
                "-m",
                "Initial commit",
            ]
        )

        with open(self.manifest_path, "a") as f:
            f.write("new: change\n")
        self._run(["git", "add", self.manifest_filename])

    def tearDown(self):
        self.test_dir.cleanup()

    def _clear_environment(self):
        unwanted_vars = [
            "EMAIL",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ]
        for var in unwanted_vars:
            os.environ.pop(var, None)

    def _run(self, cmd, **kwargs):
        subprocess.run(cmd, cwd=self.test_dir.name, check=True, **kwargs)

    def _git_show(self, fmt, rev="HEAD"):
        cmd = ["git", "show", "--no-patch", f"--pretty=format:{fmt}", rev]
        proc = subprocess.run(
            cmd, cwd=self.test_dir.name, capture_output=True, check=True
        )
        return proc.stdout.decode().strip()

    @patch("getpass.getuser", return_value="fallbackuser")
    @patch("src.main.check_call")
    def test_commit_changes_fallback_1(self, mock_check_call, mock_getuser):

        def mock_check_call_side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and "commit" in cmd and "-am" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.check_call(cmd)

        mock_check_call.side_effect = mock_check_call_side_effect

        with main.indir(self.test_dir.name):
            main.commit_changes(["Some commit"])

        author_name = self._git_show("%an")
        author_email = self._git_show("%ae")
        subject = self._git_show("%s")
        self.assertEqual(author_name, "fallbackuser")
        self.assertEqual(author_email, "fallbackuser@localhost")
        self.assertEqual(subject, "Some commit")

    @patch("getpass.getuser", return_value="fallbackuser")
    def test_commit_changes_fallback_2(self, mock_getuser):

        hooks_dir = os.path.join(self.test_dir.name, ".git", "hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        hook_path = os.path.join(hooks_dir, "pre-commit")

        with open(hook_path, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(hook_path, 0o755)

        with main.indir(self.test_dir.name):
            main.commit_changes(["Some commit"])

        author_name = self._git_show("%an")
        author_email = self._git_show("%ae")
        subject = self._git_show("%s")
        self.assertEqual(author_name, "fallbackuser")
        self.assertEqual(author_email, "fallbackuser@localhost")
        self.assertEqual(subject, "Some commit")


class TestForceForkTristate(unittest.TestCase):
    def test_neither_fork_arg(self):
        args = main.parse_cli_args([TEST_MANIFEST])
        self.assertIsNone(args.fork)

    def test_always_fork_arg(self):
        args = main.parse_cli_args(["--always-fork", TEST_MANIFEST])
        self.assertTrue(args.fork)

    def test_never_fork_arg(self):
        args = main.parse_cli_args(["--never-fork", TEST_MANIFEST])
        self.assertFalse(args.fork)

    def test_both_fork_args(self):
        with self.assertRaises(SystemExit):
            main.parse_cli_args(["--always-fork", "--never-fork", TEST_MANIFEST])


class TestRobotsTxtArg(unittest.TestCase):
    def test_enable_robots_txt_flag(self):
        with self.subTest("default is False"):
            args = main.parse_cli_args([TEST_MANIFEST])
            self.assertFalse(args.enable_robots_txt)

        with self.subTest("flag sets True"):
            args = main.parse_cli_args(["--enable-robots-txt", TEST_MANIFEST])
            self.assertTrue(args.enable_robots_txt)


class TestPrintOutdatedBrokenState(unittest.TestCase):
    def _make_broken_data(self):
        data = MagicMock()
        data.state = MagicMock()
        data.state.name = "BROKEN"
        data.State.BROKEN.__contains__ = MagicMock(return_value=True)
        data.new_version = None
        data.filename = "somelib.tar.gz"
        data.current_version._asdict.return_value = {
            "url": "https://example.com/somelib.tar.gz"
        }
        return data

    def test_broken_state_no_new_version(self):
        checker = MagicMock()
        broken = self._make_broken_data()
        broken.State.BROKEN.__ror__ = MagicMock(return_value=True)
        checker.get_outdated_external_data.return_value = [broken]

        with patch("builtins.print"):
            result = main.print_outdated_external_data(checker)
        self.assertEqual(result, 1)

    def test_broken_state_message_args(self):
        checker = MagicMock()

        broken = MagicMock()
        broken.new_version = None
        broken.filename = "somelib.tar.gz"
        broken.state = ExternalData.State.BROKEN
        broken.State = ExternalData.State
        broken.current_version._asdict.return_value = {
            "url": "https://example.com/somelib.tar.gz"
        }

        checker.get_outdated_external_data.return_value = [broken]

        with patch("builtins.print"):
            result = main.print_outdated_external_data(checker)

        self.assertEqual(result, 1)
        broken.current_version._asdict.assert_called_once()


class TestErrorSummary(unittest.IsolatedAsyncioTestCase):
    async def _check_summary(self, errors, expected):
        with (
            patch("src.main.manifest.ManifestChecker", autospec=True) as factory,
            patch("src.main.init_logging"),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
            self.assertLogs("src.main", level="INFO") as logs,
        ):
            checker = factory.return_value
            checker.build_extension = False
            checker.get_errors.return_value = errors
            checker.get_outdated_external_data.return_value = []
            result = await main.run_with_args(main.parse_cli_args([TEST_MANIFEST]))

        self.assertEqual(result, (0, len(errors), False))
        checker.check.assert_awaited_once_with(None)
        checker.get_errors.assert_called_once_with()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].getMessage(), expected)
        self.assertEqual(
            logs.records[0].levelno, logging.WARNING if errors else logging.INFO
        )
        self.assertIsNone(logs.records[0].exc_info)

    async def test_zero_errors(self):
        await self._check_summary([], "Check finished with 0 error(s)")

    async def test_one_error(self):
        await self._check_summary(
            [SourceLoadError("Invalid source in module.json")],
            "Check finished with 1 error(s)\n  1. Invalid source in module.json",
        )

    async def test_multiple_errors(self):
        await self._check_summary(
            [SourceLoadError("Invalid source"), AppdataLoadError()],
            "Check finished with 2 error(s)\n  1. Invalid source"
            "\n  2. Error loading metainfo.xml",
        )

    async def test_chained_error(self):
        cause = CheckerFetchError("Download failed")
        cause.__cause__ = ValueError("Invalid URL")
        error = SourceLoadError("Invalid source")
        error.__cause__ = cause
        await self._check_summary(
            [error],
            "Check finished with 1 error(s)"
            "\n  1. Invalid source: Download failed: Invalid URL",
        )


class TestGetManifestGitCheckout(unittest.TestCase):
    def test_no_git_checkout_raises(self):
        with tempfile.TemporaryDirectory() as d:
            fake_manifest = os.path.join(d, "manifest.yml")
            with self.assertRaises(FileNotFoundError):
                main.get_manifest_git_checkout(fake_manifest)


class TestEnsureGitSafeDirectory(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.test_dir.cleanup()

    def test_same_uid_returns_early(self):
        checkout = Path(self.test_dir.name)
        uid = os.getuid()
        with patch("os.stat") as mock_stat:
            mock_stat.return_value.st_uid = uid
            main.ensure_git_safe_directory(checkout)

    def test_different_uid_no_safe_dir_adds(self):
        checkout = Path(self.test_dir.name)
        with (
            patch("os.getuid", return_value=1000),
            patch("os.stat") as mock_stat,
            patch("subprocess.run") as mock_run,
            patch("src.main.check_call") as mock_check_call,
        ):
            mock_stat.return_value.st_uid = 9999
            err = subprocess.CalledProcessError(1, "git")
            mock_run.side_effect = err
            main.ensure_git_safe_directory(checkout)
            mock_check_call.assert_called_once()
            args = mock_check_call.call_args[0][0]
            self.assertIn("safe.directory", args)

    def test_different_uid__reraises(self):
        checkout = Path(self.test_dir.name)
        with (
            patch("os.getuid", return_value=1000),
            patch("os.stat") as mock_stat,
            patch("subprocess.run") as mock_run,
        ):
            mock_stat.return_value.st_uid = 9999
            err = subprocess.CalledProcessError(2, "git")
            mock_run.side_effect = err
            with self.assertRaises(subprocess.CalledProcessError):
                main.ensure_git_safe_directory(checkout)

    def test_different_uid_already_safe(self):
        checkout = Path(self.test_dir.name)
        with (
            patch("os.getuid", return_value=1000),
            patch("os.stat") as mock_stat,
            patch("subprocess.run") as mock_run,
            patch("src.main.check_call") as mock_check_call,
        ):
            mock_stat.return_value.st_uid = 9999
            result = MagicMock()
            result.stdout = str(checkout) + "\n"
            mock_run.return_value = result
            main.ensure_git_safe_directory(checkout)
            mock_check_call.assert_not_called()


class TestCommitMessage(unittest.TestCase):
    def test_single_change(self):
        self.assertEqual(
            main.commit_message(["foo: Update foo-1.0"]), "foo: Update foo-1.0"
        )

    def test_single_module_multiple_changes(self):
        result = main.commit_message(["foo: change 1", "foo: change 2"])
        self.assertEqual(result, "Update foo module")

    def test_two_modules(self):
        result = main.commit_message(["foo: change", "bar: change"])
        self.assertIn("foo", result)
        self.assertIn("bar", result)

    def test_subject_truncation(self):
        changes = [f"module{i}: change" for i in range(10)]
        result = main.commit_message(changes)
        self.assertLessEqual(len(result), 70)

    def test_suffix(self):
        changes = [f"module{i}: change" for i in range(10)]
        result = main.commit_message(changes)
        self.assertIn("module", result)

    def test_three_modules(self):
        changes = ["aaa: c", "bbb: c", "ccc: c", "ddd: c"]
        result = main.commit_message(changes)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_commit_message_long(self):
        long_name = "a" * 35
        changes = [f"{long_name}{i}: change" for i in range(10)]
        result = main.commit_message(changes)
        self.assertEqual(result, "Update 10 modules")


class TestOpenPR(unittest.TestCase):
    def setUp(self):
        push = patch(
            "subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        )
        self.push = push.start()
        self.addCleanup(push.stop)

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.Github")
    def test_configuration_matches_merge_policy(self, mock_github, mock_output):
        for setting in (None, True, False):
            for kind, broken, updated in (
                (ExternalData.Type.EXTRA_DATA, True, True),
                (ExternalData.Type.EXTRA_DATA, False, True),
                (ExternalData.Type.EXTRA_DATA, True, False),
                (ExternalData.Type.ARCHIVE, True, True),
            ):
                enabled = setting is True or (
                    setting is None
                    and kind == ExternalData.Type.EXTRA_DATA
                    and broken
                    and updated
                )
                with (
                    self.subTest(
                        setting=setting, kind=kind, broken=broken, updated=updated
                    ),
                    patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
                    patch(
                        "builtins.open",
                        unittest.mock.mock_open(
                            read_data=json.dumps(
                                {}
                                if setting is None
                                else {"automerge-flathubbot-prs": setting}
                            )
                        ),
                    ),
                ):
                    _, _, repo = self._setup_mocks(mock_github)
                    data = MagicMock()
                    data.Type = ExternalData.Type
                    data.State = ExternalData.State
                    data.type = kind
                    data.state = [ExternalData.State.BROKEN] if broken else []
                    data.new_version = MagicMock() if updated else None
                    checker = MagicMock()
                    checker.source_updates = []
                    checker.get_outdated_external_data.return_value = [data]
                    main.open_pr(self._make_change(), checker)
                    text = repo.create_pull.call_args.kwargs["body"]
                    if setting is not None:
                        self.assertIn("### Configuration", text)
                        self.assertIn(
                            f"Automerge is {'enabled' if enabled else 'disabled'}", text
                        )
                        self.assertIn(f"is `{str(setting).lower()}`", text)
                    elif enabled:
                        self.assertIn("### Configuration", text)
                        self.assertIn(
                            "Automerge is enabled for this broken-source update",
                            text,
                        )
                        self.assertIn("does not disable automatic merging", text)
                        self.assertIn("to `false` in `flathub.json`", text)
                    else:
                        self.assertNotIn("### Configuration", text)
                        self.assertNotIn("Automerge", text)
                        self.assertNotIn("automerge-flathubbot-prs", text)
                    if enabled:
                        self.assertIn(
                            "The checker can merge this PR on a later run", text
                        )
                        self.assertIn("Repository rules and token permissions", text)

                    pr = MagicMock(state="open", mergeable=True)
                    commit = pr.head.repo.get_commit.return_value
                    commit.get_combined_status.return_value.state = "success"
                    repo.get_pulls.return_value = [pr]
                    main.open_pr(self._make_change(), checker)
                    self.assertEqual(pr.merge.called, enabled)
                    pr.edit.assert_not_called()

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.Github")
    def test_test_build_guidance_is_flathub_only(self, mock_github, mock_output):
        for name in ("flathub/org.example.App", "owner/repo", "flathub-other/repo"):
            with (
                self.subTest(name=name),
                patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
                patch("builtins.open", side_effect=FileNotFoundError),
            ):
                _, _, repo = self._setup_mocks(mock_github)
                repo.full_name = name
                main.open_pr(self._make_change())
                text = repo.create_pull.call_args.kwargs["body"]
                self.assertNotIn("### Configuration", text)
                self.assertNotIn("Automerge", text)
                self.assertEqual("`bot, build`" in text, name.startswith("flathub/"))

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.Github")
    def test_automerge_requires_status_and_mergeability(self, mock_github, mock_output):
        for status, mergeable in (
            ("pending", True),
            ("failure", True),
            ("success", False),
            ("success", None),
        ):
            with (
                self.subTest(status=status, mergeable=mergeable),
                patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
                patch(
                    "builtins.open",
                    unittest.mock.mock_open(
                        read_data='{"automerge-flathubbot-prs": true}'
                    ),
                ),
            ):
                pr = MagicMock(state="open", mergeable=mergeable)
                commit = pr.head.repo.get_commit.return_value
                commit.get_combined_status.return_value.state = status
                self._setup_mocks(mock_github, open_prs=[pr])
                main.open_pr(self._make_change())
                pr.merge.assert_not_called()

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_source_details_replace_body(self, mock_github, mock_call, mock_output):
        _, _, repo = self._setup_mocks(mock_github)
        checker = MagicMock()
        checker.source_updates = [SourceUpdate("foo", "1", "2", None)]
        checker.get_outdated_external_data.return_value = []
        for body in (None, "foo: Update foo to 2"):
            with (
                self.subTest(body=body),
                patch.dict(
                    os.environ,
                    {
                        "GITHUB_TOKEN": "fake-token",
                        "GITHUB_RUN_ID": "123",
                        "GITHUB_REPOSITORY": "owner/repo",
                    },
                ),
                patch("builtins.open", side_effect=FileNotFoundError),
            ):
                main.open_pr(self._make_change()._replace(body=body), checker)
                text = repo.create_pull.call_args.kwargs["body"]
                self.assertIn("### Source updates", text)
                self.assertIn(
                    "| foo | <code>1</code> → <code>2</code> | Unavailable |", text
                )
                self.assertIn(main.DISCLAIMER, text)
                self.assertIn("https://github.com/owner/repo/actions/runs/123", text)
                if body:
                    self.assertNotIn(body, text)

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_body_without_source_details(self, mock_github, mock_call, mock_output):
        _, _, repo = self._setup_mocks(mock_github)
        checker = MagicMock()
        checker.source_updates = []
        checker.get_outdated_external_data.return_value = []
        change = self._make_change()
        for manifest_checker in (None, checker):
            with (
                self.subTest(manifest_checker=manifest_checker),
                patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
                patch("builtins.open", side_effect=FileNotFoundError),
            ):
                main.open_pr(change, manifest_checker)
                text = repo.create_pull.call_args.kwargs["body"]
                self.assertTrue(
                    text.startswith("### Updates\n\n" + change.body + "\n\n")
                )
                self.assertNotIn("### Source updates", text)
                self.assertIn(main.DISCLAIMER, text)

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.Github")
    def test_single_update_uses_subject_when_body_is_missing(
        self, mock_github, mock_output
    ):
        _, _, repo = self._setup_mocks(mock_github)
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
            patch("builtins.open", side_effect=FileNotFoundError),
        ):
            change = self._make_change()._replace(body=None)
            main.open_pr(change)
        text = repo.create_pull.call_args.kwargs["body"]
        self.assertTrue(text.startswith(f"### Updates\n\n{change.subject}\n\n"))
        self.assertIn(main.DISCLAIMER, text)

    def _make_change(self):
        return main.CommittedChanges(
            subject="Update foo",
            body="foo: update details",
            commit="abc1234",
            branch="update-abc1234",
            base_branch="main",
        )

    def _setup_mocks(
        self, mock_github, open_prs=None, closed_prs=None, push_permission=True
    ):
        g = MagicMock()
        mock_github.return_value = g
        user = MagicMock()
        g.get_user.return_value = user

        origin_repo = MagicMock()
        origin_repo.permissions.push = push_permission
        origin_repo.default_branch = "main"
        origin_repo.full_name = "owner/repo"
        origin_repo.html_url = "https://github.com/owner/repo"
        g.get_repo.return_value = origin_repo

        all_prs = (closed_prs or []) + (open_prs or [])
        origin_repo.get_pulls.return_value = all_prs

        return g, user, origin_repo

    def test_missing_github_token_exits(self):
        change = main.CommittedChanges(
            subject="Update foo",
            body=None,
            commit="abc1234",
            branch="update-abc1234",
            base_branch="main",
        )
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GITHUB_TOKEN", None)
            with self.assertRaises(SystemExit):
                main.open_pr(change)

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.Github")
    def test_closed_pr_returns_early(self, mock_github, mock_check_output):
        closed_pr = MagicMock()
        closed_pr.state = "closed"
        closed_pr.is_merged.return_value = False
        closed_pr.html_url = "https://github.com/owner/repo/pull/1"

        self._setup_mocks(mock_github, closed_prs=[closed_pr])

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change())

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_no_existing_prs_creates_pr(
        self, mock_github, mock_check_call, mock_check_output
    ):
        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[], closed_prs=[])
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/2"
        origin_repo.create_pull.return_value = pr

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change(), pr_labels=["label1"])

        self.push.assert_called_once_with(
            [
                "git",
                "push",
                "--porcelain",
                "--force-with-lease=refs/heads/update-abc1234:",
                "https://x-access-token:fake-token@github.com/owner/repo",
                "HEAD:refs/heads/update-abc1234",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        origin_repo.create_pull.assert_called_once()
        pr.set_labels.assert_called_once_with("label1")

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_open_pr_automerge_config(
        self, mock_github, mock_check_call, mock_check_output
    ):
        open_pr = MagicMock()
        open_pr.state = "open"
        open_pr.html_url = "https://github.com/owner/repo/pull/3"
        open_pr.mergeable = True
        pr_commit = MagicMock()
        pr_commit.get_combined_status.return_value.state = "success"
        open_pr.head.repo.get_commit.return_value = pr_commit

        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[open_pr])

        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
            patch(
                "builtins.open",
                unittest.mock.mock_open(read_data='{"automerge-flathubbot-prs": true}'),
            ),
        ):
            main.open_pr(self._make_change())

        open_pr.merge.assert_called_once_with(merge_method="rebase")
        open_pr.create_issue_comment.assert_called_once_with(
            main.AUTOMERGE_DUE_TO_CONFIG
        )

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_open_pr_force_automerge_broken_urls(
        self, mock_github, mock_check_call, mock_check_output
    ):
        open_pr_mock = MagicMock()
        open_pr_mock.state = "open"
        open_pr_mock.html_url = "https://github.com/owner/repo/pull/4"
        open_pr_mock.mergeable = True
        pr_commit = MagicMock()
        pr_commit.get_combined_status.return_value.state = "success"
        open_pr_mock.head.repo.get_commit.return_value = pr_commit

        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[open_pr_mock])

        broken_data = MagicMock()
        broken_data.Type = ExternalData.Type
        broken_data.State = ExternalData.State
        broken_data.type = ExternalData.Type.EXTRA_DATA
        broken_data.state = [ExternalData.State.BROKEN]
        broken_data.new_version = MagicMock()

        checker = MagicMock()
        checker.get_outdated_external_data.return_value = [broken_data]

        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
            patch("builtins.open", side_effect=FileNotFoundError),
        ):
            main.open_pr(self._make_change(), manifest_checker=checker)

            open_pr_mock.create_issue_comment.assert_called_once_with(
                main.AUTOMERGE_DUE_TO_BROKEN_URLS
            )
            open_pr_mock.merge.assert_called_once_with(merge_method="rebase")

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_always_fork(self, mock_github, mock_check_call, mock_check_output):
        _, user, origin_repo = self._setup_mocks(
            mock_github, open_prs=[], closed_prs=[]
        )
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/5"
        origin_repo.create_pull.return_value = pr

        fork_repo = MagicMock()
        fork_repo.full_name = "forkowner/repo"
        fork_repo.owner.login = "forkowner"
        origin_repo.create_fork.return_value = fork_repo

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change(), fork=True)
        origin_repo.create_fork.assert_called_once_with()

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_never_fork(self, mock_github, mock_check_call, mock_check_output):
        _, user, origin_repo = self._setup_mocks(
            mock_github, open_prs=[], closed_prs=[], push_permission=False
        )
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/6"
        origin_repo.create_pull.return_value = pr

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change(), fork=False)

        user.create_fork.assert_not_called()

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_non_default_base_branch_prefixes_subject(
        self, mock_github, mock_check_call, mock_check_output
    ):
        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[], closed_prs=[])
        origin_repo.default_branch = "main"
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/7"
        origin_repo.create_pull.return_value = pr

        change = main.CommittedChanges(
            subject="Update foo",
            body=None,
            commit="abc1234",
            branch="update-abc1234",
            base_branch="stable-1.0",
        )

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(change)

        call_kwargs = origin_repo.create_pull.call_args[1]
        self.assertTrue(call_kwargs["title"].startswith("[stable-1.0]"))

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_github_run_id_appends_log_url(
        self, mock_github, mock_check_call, mock_check_output
    ):
        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[], closed_prs=[])
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/8"
        origin_repo.create_pull.return_value = pr

        with patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "fake-token",
                "GITHUB_RUN_ID": "12345",
                "GITHUB_REPOSITORY": "owner/repo",
            },
        ):
            main.open_pr(self._make_change())

        call_kwargs = origin_repo.create_pull.call_args[1]
        self.assertIn("actions/runs/12345", call_kwargs["body"])

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_no_push_permission_creates_fork(
        self, mock_github, mock_check_call, mock_check_output
    ):
        _, user, origin_repo = self._setup_mocks(
            mock_github, open_prs=[], closed_prs=[], push_permission=False
        )
        fork_repo = MagicMock()
        fork_repo.full_name = "forkowner/repo"
        fork_repo.owner.login = "forkowner"
        origin_repo.create_fork.return_value = fork_repo

        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/99"
        origin_repo.create_pull.return_value = pr

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change(), fork=None)

        origin_repo.create_fork.assert_called_once_with()


class TestMain(unittest.IsolatedAsyncioTestCase):
    @patch("src.main.asyncio")
    @patch("src.main.parse_cli_args")
    def test_main_error_result(self, mock_parse, mock_asyncio):
        mock_parse.return_value = MagicMock(check_outdated=False)
        mock_asyncio.run.return_value = (-1, -1, False)
        with self.assertRaises(SystemExit) as ctx:
            main.main()
        self.assertTrue(int(ctx.exception.code) & int(main.ResultCode.ERROR))

    @patch("src.main.asyncio")
    @patch("src.main.parse_cli_args")
    def test_main_outdated_not_updated(self, mock_parse, mock_asyncio):
        args = MagicMock(check_outdated=True)
        mock_parse.return_value = args
        mock_asyncio.run.return_value = (3, 0, False)
        with self.assertRaises(SystemExit) as ctx:
            main.main()
        self.assertTrue(int(ctx.exception.code) & int(main.ResultCode.OUTDATED))

    @patch("src.main.asyncio")
    @patch("src.main.parse_cli_args")
    def test_main_success(self, mock_parse, mock_asyncio):
        args = MagicMock(check_outdated=False)
        mock_parse.return_value = args
        mock_asyncio.run.return_value = (0, 0, False)
        with self.assertRaises(SystemExit) as ctx:
            main.main()
        self.assertEqual(int(ctx.exception.code), int(main.ResultCode.SUCCESS))

    @patch("src.main.asyncio")
    @patch("src.main.parse_cli_args")
    def test_main_errors_num_sets_error(self, mock_parse, mock_asyncio):
        args = MagicMock(check_outdated=False)
        mock_parse.return_value = args
        mock_asyncio.run.return_value = (0, 2, False)
        with self.assertRaises(SystemExit) as ctx:
            main.main()
        self.assertTrue(int(ctx.exception.code) & int(main.ResultCode.ERROR))

    @patch("src.main.open_pr")
    @patch("src.main.commit_changes")
    @patch("src.main.ensure_git_safe_directory")
    @patch("src.main.get_manifest_git_checkout")
    @patch("src.main.indir")
    async def test_run_with_args_calls_open_pr(
        self, mock_indir, mock_checkout, mock_safe, mock_commit, mock_open_pr
    ):
        mock_indir.return_value = MagicMock(
            __enter__=lambda s: s, __exit__=MagicMock(return_value=False)
        )
        mock_checkout.return_value = Path("/fake/checkout")
        committed = main.CommittedChanges(
            subject="Update foo",
            body=None,
            commit="abc123",
            branch="update-abc123",
            base_branch="main",
        )
        mock_commit.return_value = committed

        with patch("src.main.manifest.ManifestChecker") as MockChecker:
            checker = MockChecker.return_value
            checker.check = AsyncMock(return_value=None)

            mock_data = MagicMock()

            mock_data.__class__ = ExternalData

            mock_data.type = ExternalData.Type.EXTRA_DATA
            mock_data.State = ExternalData.State

            mock_data.state = MagicMock()
            mock_data.state.name = "UNKNOWN"

            mock_data.filename = "foo.tar.gz"

            mock_data.new_version._asdict.return_value = {
                "url": "https://example.com/foo.tar.gz",
                "version": "1.0",
                "timestamp": "now",
            }
            mock_data.new_version.checksum._asdict.return_value = {
                "md5": "1",
                "sha1": "2",
                "sha256": "3",
                "sha512": "4",
                "size": 100,
            }

            checker.get_outdated_external_data.return_value = [mock_data]

            checker.update_manifests.return_value = ["foo: update"]
            checker.get_errors.return_value = []

            args = main.parse_cli_args(["--update", "/fake/manifest.yml"])
            args.commit_only = False
            args.commit_to_current_branch = False

            with patch("builtins.print"):
                result = await main.run_with_args(args)

        mock_open_pr.assert_called_once()
        self.assertEqual(result[2], True)


if __name__ == "__main__":
    unittest.main()
