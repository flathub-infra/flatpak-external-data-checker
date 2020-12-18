import os
import unittest

from src.checker import ManifestChecker
from src.lib.utils import init_logging

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.flatpak.Flatpak.yml")


class TestAnityaChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        self.assertEqual(len(ext_data), 1)
        data = ext_data[0]
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https://github.com/flatpak/flatpak/releases/download/[\w\d.]+/flatpak-[\w\d.]+.tar.xz$",
        )
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, str)
        self.assertNotEqual(
            data.new_version.checksum,
            "7926625df7c2282a5ee1a8b3c317af53d40a663b1bc6b18a2dc8747e265085b0",
        )


if __name__ == "__main__":
    unittest.main()
