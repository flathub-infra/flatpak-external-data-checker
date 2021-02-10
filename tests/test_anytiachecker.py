import os
import unittest

from src.checker import ManifestChecker
from src.lib.externaldata import ExternalFile, ExternalGitRef
from src.lib.utils import init_logging

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.flatpak.Flatpak.yml")


class TestAnityaChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        self.assertEqual(len(ext_data), 2)
        for data in ext_data:
            if data.filename == "flatpak-1.8.2.tar.xz":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalFile)
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
            elif data.filename == "ostree.git":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertIsNotNone(data.new_version.commit)
                self.assertIsNotNone(data.new_version.tag)
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
                self.assertNotEqual(data.new_version.tag, data.current_version.tag)
                self.assertIsNotNone(data.new_version.version)
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
            else:
                self.fail(f"Unknown data {data.filename}")


if __name__ == "__main__":
    unittest.main()
