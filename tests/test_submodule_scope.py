import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.lib.errors import ManifestScopeError
from src.manifest import ManifestChecker


class TestSubmoduleScope(unittest.TestCase):
    def test_includes_do_not_load_submodules(self):
        for initialized in (False, True):
            for kind in ("module", "sources"):
                for symlink in (False, True):
                    with (
                        self.subTest(
                            initialized=initialized, kind=kind, symlink=symlink
                        ),
                        tempfile.TemporaryDirectory() as directory,
                    ):
                        root = Path(directory)
                        subprocess.run(
                            ["git", "init", directory], check=True, capture_output=True
                        )
                        subprocess.run(
                            [
                                "git",
                                "-C",
                                directory,
                                "update-index",
                                "--add",
                                "--cacheinfo",
                                "160000",
                                "a" * 40,
                                "shared modules",
                            ],
                            check=True,
                        )
                        shared = root / "shared modules"
                        shared.mkdir()
                        if initialized:
                            (shared / ".git").write_text("gitdir: unused\n")
                            # Loading this file would fail even before checking sources.
                            (shared / "include.json").write_text("not valid JSON")
                        if symlink:
                            (root / "alias").symlink_to(
                                shared, target_is_directory=True
                            )
                        include = (
                            "alias/include.json"
                            if symlink
                            else "shared modules/include.json"
                        )
                        source = {
                            "type": "file",
                            "url": "https://example.com/local",
                            "sha256": "b" * 64,
                        }
                        (root / "local.json").write_text(json.dumps(source))
                        module = {"name": "local", "sources": ["local.json"]}
                        if kind == "module":
                            modules = [include, module]
                        else:
                            module["sources"].insert(0, include)
                            modules = [module]
                        path = root / "app.json"
                        path.write_text(
                            json.dumps({"id": "org.test.App", "modules": modules})
                        )
                        with self.assertLogs("src.manifest", level="INFO") as logs:
                            checker = ManifestChecker(str(path))
                        self.assertTrue(any("Loading" in line for line in logs.output))
                        self.assertTrue(
                            any(
                                "coverage is incomplete" in line for line in logs.output
                            )
                        )
                        self.assertEqual(len(checker.get_external_data()), 1)
                        self.assertEqual(checker.get_errors(), [])
                        self.assertEqual(len(checker.get_report_errors()), 1)
                        self.assertIsInstance(
                            checker.get_report_errors()[0], ManifestScopeError
                        )
                        report = checker.get_source_report()
                        self.assertEqual(len(report), 1)
                        self.assertEqual(report[0]["status"], "unchecked")
                        self.assertEqual(report[0]["manifest"], "local.json")
