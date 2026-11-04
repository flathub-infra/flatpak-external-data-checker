import os
import unittest
from unittest import mock

from src.checkers.jetbrainschecker import JetBrainsChecker
from src.lib.checksums import MultiDigest
from src.lib.externaldata import ExternalBase
from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from tests.support import upstream_test

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.jetbrains.PhpStorm.json")


class TestJetBrainsChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    @upstream_test
    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        data = self._find_by_filename(ext_data, "phpstorm.tar.gz")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "phpstorm.tar.gz")
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https://download\.jetbrains\.com/webide/PhpStorm-.+\.tar\.gz$",
        )
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, MultiDigest)
        self.assertNotEqual(
            data.new_version.checksum,
            MultiDigest(
                sha256="0000000000000000000000000000000000000000000000000000000000000000"
            ),
        )

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        return None


class TestJetBrainsReleaseLinks(unittest.IsolatedAsyncioTestCase):
    async def test_release_notes_from_selected_release(self):
        for notes in ("https://example.org/notes/2", None, "javascript:alert(1)", 123):
            with self.subTest(notes=notes):
                source = ExternalBase.from_source(
                    "app.json",
                    {
                        "type": "archive",
                        "url": "https://example.org/app-1.tar.gz",
                        "sha256": "old",
                        "x-checker-data": {"type": "jetbrains", "code": "IIU"},
                    },
                )
                release = {
                    "version": "2",
                    "date": "2026-01-01",
                    "notesLink": notes,
                    "downloads": {
                        "linux": {
                            "link": "https://example.org/app-2.tar.gz",
                            "size": 42,
                            "checksumLink": "https://example.org/app-2.sha256",
                        }
                    },
                }
                metadata = mock.AsyncMock()
                metadata.json.return_value = {"IIU": [release]}
                checksum = mock.AsyncMock()
                checksum.text.return_value = "abc  app-2.tar.gz"
                checker = JetBrainsChecker.__new__(JetBrainsChecker)
                checker.robots_cache = None
                checker.session = mock.MagicMock()
                checker.session.get.return_value.__aenter__ = mock.AsyncMock(
                    side_effect=[metadata, checksum]
                )
                await checker.check(source)
                self.assertEqual(source.new_version.version, "2")
                expected = notes if notes == "https://example.org/notes/2" else None
                self.assertEqual(source.new_release_url, expected)
                self.assertEqual(checker.session.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
