import unittest
import os

from src.manifest import ManifestChecker
from src.lib.utils import init_logging
from src.lib.checksums import MultiDigest


class TestURLChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    async def test_check_appimage(self):
        checker = ManifestChecker(
            os.path.join(os.path.dirname(__file__), "com.unity.UnityHub.yaml")
        )
        ext_data = await checker.check()

        data = self._find_by_filename(ext_data, "UnityHubSetup.AppImage")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "UnityHubSetup.AppImage")
        self.assertIsNotNone(data.new_version)
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, MultiDigest)
        self.assertNotEqual(
            data.new_version.checksum,
            MultiDigest(
                sha256="0000000000000000000000000000000000000000000000000000000000000000"  # noqa: E501
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
                sha256="0000000000000000000000000000000000000000000000000000000000000000"  # noqa: E501
            ),
        )
        self.assertIsNotNone(data.new_version.version)

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        else:
            return None


if __name__ == "__main__":
    unittest.main()
