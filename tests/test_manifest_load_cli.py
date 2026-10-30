import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import main


class TestManifestLoadCLI(unittest.TestCase):
    def test_invalid_parent_graph(self):
        for parent_id, detail in [
            ("tdesktop-git-0", 'parent source with ID "tdesktop-git-0" not found'),
            ("patches", "inheritance loop detected"),
        ]:
            with (
                self.subTest(parent_id=parent_id),
                tempfile.TemporaryDirectory() as tmp,
            ):
                manifest = Path(tmp) / "io.github.kotatogram.json"
                manifest.write_text(
                    json.dumps(
                        {
                            "type": "git",
                            "url": "https://example.invalid/tdesktop_rust/patches.git",
                            "tag": "v1.0",
                            "x-checker-data": {
                                "type": "git",
                                "source-id": "patches",
                                "parent-id": parent_id,
                            },
                        }
                    )
                )
                original = manifest.read_bytes()
                result = subprocess.run(
                    [
                        sys.executable,
                        str(
                            Path(__file__).resolve().parents[1]
                            / "flatpak-external-data-checker"
                        ),
                        "--edit-only",
                        "--check-outdated",
                        str(manifest),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
                self.assertEqual(result.returncode, main.ResultCode.ERROR)
                self.assertNotIn("Traceback", result.stderr)
                self.assertIn(str(manifest), result.stderr)
                self.assertIn("git patches.git", result.stderr)
                self.assertIn(detail, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(manifest.read_bytes(), original)

    def test_unexpected_construction_error_propagates(self):
        args = main.parse_cli_args(["unused.json"])
        with (
            patch("src.main.init_logging"),
            patch("src.main.manifest.ManifestChecker", side_effect=RuntimeError("bug")),
            self.assertRaisesRegex(RuntimeError, "bug"),
        ):
            asyncio.run(main.run_with_args(args))
