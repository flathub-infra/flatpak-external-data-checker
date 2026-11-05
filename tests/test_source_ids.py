import json
import tempfile
import unittest
from pathlib import Path

from src.lib.errors import ManifestLoadError
from src.lint import ManifestLinter
from src.manifest import ManifestChecker


class TestSourceIDs(unittest.TestCase):
    def test_invalid_ids_are_rejected_by_loader_and_linter(self):
        for ids in (("",), ("same", "same")):
            with self.subTest(ids=ids), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "app.json"
                path.write_text(
                    json.dumps(
                        {
                            "id": "org.example.App",
                            "modules": [
                                {
                                    "name": "app",
                                    "sources": [
                                        {
                                            "type": "archive",
                                            "url": f"https://example.com/{index}.tar.gz",
                                            "sha256": "a" * 64,
                                            "x-checker-data": {
                                                "type": "rotating-url",
                                                "url": "https://example.com/latest",
                                                "source-id": ident,
                                            },
                                        }
                                        for index, ident in enumerate(ids)
                                    ],
                                }
                            ],
                        }
                    )
                )
                if len(ids) > 1:
                    with self.assertRaises(ManifestLoadError):
                        ManifestChecker(str(path))
                else:
                    checker = ManifestChecker(str(path))
                    self.assertTrue(checker.get_errors())
                    self.assertEqual(checker.get_external_data(), [])
                self.assertTrue(ManifestLinter(100000).lint(path))
