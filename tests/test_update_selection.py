import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.lib.checksums import MultiDigest
from src.manifest import CheckerOptions, ManifestChecker


class TestUpdateSelection(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "app.json"
        self.path.write_text(
            json.dumps(
                {
                    "id": "org.example.App",
                    "modules": [
                        {
                            "name": "app",
                            "sources": [
                                {
                                    "type": "archive",
                                    "url": f"https://example.com/{name}-1.tar.gz",
                                    "sha256": "a" * 64,
                                    "x-checker-data": {
                                        "type": "rotating-url",
                                        "url": f"https://example.com/{name}/latest",
                                        "is-main-source": name == "main",
                                    },
                                }
                                for name in ("main", "dependency")
                            ],
                        }
                    ],
                }
            )
        )
        self.checker = ManifestChecker(str(self.path))
        self.main, self.dependency = self.checker.get_external_data()
        for data in (self.main, self.dependency):
            data.new_version = data.current_version._replace(
                version="2", checksum=MultiDigest(sha256="b" * 64)
            )

    def test_planning_does_not_mutate_sources_or_files(self):
        before = self.path.read_bytes()
        source = dict(self.main.source)
        self.assertEqual(
            self.checker.get_planned_updates(), [self.main, self.dependency]
        )
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.main.source, source)

    def test_filtering_limits_writes_appdata_and_reported_updates(self):
        self.path.with_name("org.example.App.metainfo.xml").write_text("<component/>")
        with patch("src.manifest.add_release_to_file") as release:
            self.checker.update_manifests([self.dependency])
        release.assert_not_called()
        sources = json.loads(self.path.read_text())["modules"][0]["sources"]
        self.assertEqual(sources[0]["sha256"], "a" * 64)
        self.assertEqual(sources[1]["sha256"], "b" * 64)
        self.assertEqual(self.checker.get_applied_external_data(), [self.dependency])
        self.assertEqual(len(self.checker.source_updates), 1)

    def test_filtered_main_source_cannot_satisfy_importance_gate(self):
        self.checker.opts = CheckerOptions(require_important_update=True)
        before = self.path.read_bytes()
        self.assertEqual(self.checker.update_manifests([self.dependency]), [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.checker.get_applied_external_data(), [])

    def test_empty_selection_writes_nothing(self):
        before = self.path.read_bytes()
        self.assertEqual(self.checker.update_manifests([]), [])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.checker.source_updates, [])
