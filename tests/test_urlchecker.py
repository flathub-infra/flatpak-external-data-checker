import os
import unittest
from unittest import mock
from unittest.mock import MagicMock

from src.checkers.urlchecker import is_same_version
from src.lib.checksums import MultiDigest
from src.lib.utils import init_logging
from src.manifest import ManifestChecker


def _make_version(url, version=None):
    v = MagicMock()
    v.url = url
    v.version = version
    return v


class TestURLChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    async def test_check_appimage(self):
        checker = ManifestChecker(
            os.path.join(os.path.dirname(__file__), "com.heroicgameslauncher.hgl.yaml")
        )
        ext_data = await checker.check()

        filename = "Heroic-2.21.0-linux-x86_64.AppImage"
        data = self._find_by_filename(ext_data, filename)

        self.assertIsNotNone(data)
        self.assertEqual(data.filename, filename)
        self.assertIsNotNone(data.new_version)
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
        self.assertIsNotNone(data.new_version.version)

    async def test_check_deb(self):
        checker = ManifestChecker(
            os.path.join(os.path.dirname(__file__), "com.google.Chrome.yaml")
        )
        ext_data = await checker.check()

        data = self._find_by_filename(ext_data, "chrome.deb")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "chrome.deb")
        self.assertIsNotNone(data.new_version)
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
        self.assertIsNotNone(data.new_version.version)

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        return None


CHECKER_DATA_WITH_PATTERN = {"pattern": r"https://example\.com/prog_([\d.]+)\.tar\.gz"}
CHECKER_DATA_NO_PATTERN = {}
CURRENT_URL = "https://example.com/prog_1.2.3.tar.gz"


class TestIsSameVersion(unittest.TestCase):
    def test_none_new_version_returns_false(self):
        self.assertFalse(is_same_version(CHECKER_DATA_NO_PATTERN, CURRENT_URL, None))

    def test_no_version_same_url_returns_true(self):
        nv = _make_version(url=CURRENT_URL, version=None)
        self.assertTrue(is_same_version(CHECKER_DATA_NO_PATTERN, CURRENT_URL, nv))

    def test_no_version_different_url_returns_false(self):
        nv = _make_version(url="https://example.com/prog_1.2.4.tar.gz", version=None)
        self.assertFalse(is_same_version(CHECKER_DATA_NO_PATTERN, CURRENT_URL, nv))

    def test_pattern_no_match_on_current_url_same_url(self):
        bad_url = "https://other.example.com/something"
        nv = _make_version(url=bad_url, version="1.2.3")
        self.assertTrue(is_same_version(CHECKER_DATA_WITH_PATTERN, bad_url, nv))

    def test_pattern_no_match_on_current_url_different_url(self):
        bad_url = "https://other.example.com/something"
        nv = _make_version(
            url="https://other.example.com/something-else", version="1.2.4"
        )
        self.assertFalse(is_same_version(CHECKER_DATA_WITH_PATTERN, bad_url, nv))

    def test_pattern_match_same_version(self):
        nv = _make_version(url="https://example.com/prog_1.2.3.tar.gz", version="1.2.3")
        self.assertTrue(is_same_version(CHECKER_DATA_WITH_PATTERN, CURRENT_URL, nv))

    def test_pattern_match_different_version(self):
        nv = _make_version(url="https://example.com/prog_1.2.4.tar.gz", version="1.2.4")
        self.assertFalse(is_same_version(CHECKER_DATA_WITH_PATTERN, CURRENT_URL, nv))


if __name__ == "__main__":
    unittest.main()
