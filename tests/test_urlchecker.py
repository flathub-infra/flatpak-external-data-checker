import unittest
import os

from src.checker import ManifestChecker
from src.lib.utils import init_logging

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.unity.UnityHub.yaml")


class TestURLChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        data = self._find_by_filename(ext_data, "UnityHubSetup.AppImage")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "UnityHubSetup.AppImage")
        self.assertIsNotNone(data.new_version)
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, str)
        self.assertNotEqual(
            data.new_version.checksum,
            "0000000000000000000000000000000000000000000000000000000000000000",
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
