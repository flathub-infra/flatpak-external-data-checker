import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from lxml.etree import XMLSyntaxError

from src import manifest
from src.lib.checksums import MultiDigest
from src.lib.errors import AppdataError
from src.lib.externaldata import ExternalFile


class TestManifest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.mf_path = os.path.join(self.test_dir.name, "test.json")

    def tearDown(self):
        self.test_dir.cleanup()

    def test_find_appdata_file_not_found(self):
        self.assertIsNone(
            manifest.find_appdata_file(self.test_dir.name, "org.test.App")
        )

    def test_manifest_load_unknown_kind(self):
        with open(self.mf_path, "w") as f:
            json.dump({}, f)
        with self.assertRaises(manifest.ManifestLoadError):
            manifest.ManifestChecker(self.mf_path)

    def test_manifest_file_too_large(self):
        with open(self.mf_path, "w") as f:
            json.dump({"id": "org.test.App"}, f)
        opts = manifest.CheckerOptions(max_manifest_size=1)
        with self.assertRaises(manifest.ManifestFileTooLarge):
            manifest.ManifestChecker(self.mf_path, opts)

    def test_child_modules_not_a_list(self):
        with open(self.mf_path, "w") as f:
            json.dump(
                {
                    "id": "org.test.App",
                    "modules": [{"name": "mod1", "modules": {"dict": "not-list"}}],
                },
                f,
            )
        checker = manifest.ManifestChecker(self.mf_path)

        self.assertEqual(len(checker._modules[self.mf_path]), 1)
        self.assertEqual(checker._modules[self.mf_path][0].name, "mod1")

    def test_nested_external_sources_raises(self):
        ext1 = os.path.join(self.test_dir.name, "ext1.json")
        with open(ext1, "w") as f:
            json.dump(["ext2.json"], f)

        with open(self.mf_path, "w") as f:
            json.dump(
                {
                    "id": "org.test.App",
                    "modules": [{"name": "mod", "sources": ["ext1.json"]}],
                },
                f,
            )

        with self.assertRaises(manifest.ManifestLoadError):
            manifest.ManifestChecker(self.mf_path)

    @patch("src.manifest.os.stat")
    def test_external_source_too_large(self, mock_stat):
        ext1 = os.path.join(self.test_dir.name, "ext1.json")
        with open(ext1, "w") as f:
            json.dump({"type": "file", "url": "http://a", "sha256": "b"}, f)

        with open(self.mf_path, "w") as f:
            json.dump(
                {
                    "id": "org.test.App",
                    "modules": [{"name": "mod", "sources": ["ext1.json"]}],
                },
                f,
            )

        def fake_stat(path):
            mock_obj = MagicMock()
            mock_obj.st_size = 99999999 if path.endswith("ext1.json") else 100
            return mock_obj

        mock_stat.side_effect = fake_stat
        checker = manifest.ManifestChecker(self.mf_path)
        self.assertEqual(len(checker.get_external_data()), 0)

    async def test_check_skips_known_state(self):
        with open(self.mf_path, "w") as f:
            json.dump(
                {
                    "id": "org.test.App",
                    "modules": [
                        {
                            "name": "mod",
                            "sources": [
                                {"type": "file", "url": "http://a", "sha256": "b"}
                            ],
                        }
                    ],
                },
                f,
            )
        checker = manifest.ManifestChecker(self.mf_path)
        data = checker.get_external_data()[0]
        data.state = data.State.VALID

        with patch.object(checker, "_check_data") as mock_check:
            await checker.check()
            mock_check.assert_not_called()

    def test_update_manifest_no_version(self):
        with open(self.mf_path, "w") as f:
            json.dump(
                {
                    "id": "org.test.App",
                    "modules": [
                        {
                            "name": "mod",
                            "sources": [
                                {
                                    "type": "file",
                                    "url": "http://example.com/my-file.txt",
                                    "sha256": "b",
                                }
                            ],
                        }
                    ],
                },
                f,
            )
        checker = manifest.ManifestChecker(self.mf_path)
        data = checker.get_external_data()[0]
        data.new_version = ExternalFile(
            url="http://new",
            checksum=MultiDigest(sha256="c"),
            size=None,
            version=None,
            timestamp=None,
        )

        changes = {}
        checker._update_manifest(self.mf_path, [data], changes)

        self.assertIn("mod: Update my-file.txt", changes)

    def test_update_appdata_not_found(self):
        with open(self.mf_path, "w") as f:
            json.dump({"id": "org.test.App"}, f)
        checker = manifest.ManifestChecker(self.mf_path)
        with self.assertRaises(manifest.AppdataNotFound):
            checker._update_appdata()

    def test_update_appdata_no_main_source(self):
        with open(self.mf_path, "w") as f:
            json.dump({"id": "org.test.App"}, f)

        appdata_path = os.path.join(self.test_dir.name, "org.test.App.appdata.xml")
        with open(appdata_path, "w") as f:
            f.write("<component></component>")

        checker = manifest.ManifestChecker(self.mf_path)
        with patch("src.manifest.log.error") as mock_log:
            checker._update_appdata()
            mock_log.assert_called_once()

    @patch("src.manifest.add_release_to_file")
    def test_update_appdata_xml_syntax_error(self, mock_add_release):
        with open(self.mf_path, "w") as f:
            json.dump(
                {
                    "id": "org.test.App",
                    "modules": [
                        {
                            "name": "mod",
                            "sources": [
                                {"type": "file", "url": "http://a/b", "sha256": "c"}
                            ],
                        }
                    ],
                },
                f,
            )

        appdata_path = os.path.join(self.test_dir.name, "org.test.App.appdata.xml")
        with open(appdata_path, "w") as f:
            f.write("<component><invalid>")

        checker = manifest.ManifestChecker(self.mf_path)
        data = checker.get_external_data()[0]
        data.checker_data["is-main-source"] = True
        data.new_version = ExternalFile(
            url="http://new",
            checksum=MultiDigest(sha256="d"),
            size=None,
            version="2.0",
            timestamp=None,
        )
        mock_add_release.side_effect = XMLSyntaxError("Simulated invalid XML", 1, 0, 0)

        with self.assertRaises(manifest.AppdataLoadError):
            checker._update_appdata()

    def test_update_manifests_appdata_error(self):
        with open(self.mf_path, "w") as f:
            json.dump(
                {
                    "id": "org.test.App",
                    "modules": [
                        {
                            "name": "mod",
                            "sources": [
                                {"type": "file", "url": "http://a/b", "sha256": "c"}
                            ],
                        }
                    ],
                },
                f,
            )

        checker = manifest.ManifestChecker(self.mf_path)
        data = checker.get_external_data()[0]
        data.new_version = ExternalFile(
            url="http://new",
            checksum=MultiDigest(sha256="d"),
            size=None,
            version="2.0",
            timestamp=None,
        )

        with patch.object(
            checker,
            "_update_appdata",
            side_effect=AppdataError("Simulated appdata crash"),
        ):
            changes = checker.update_manifests()

        self.assertEqual(len(changes), 1)
        self.assertIsInstance(checker.get_errors()[0], AppdataError)


if __name__ == "__main__":
    unittest.main()
