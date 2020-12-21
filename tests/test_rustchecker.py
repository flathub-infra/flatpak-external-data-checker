import os
import unittest

from src.checker import ManifestChecker
from src.lib.utils import init_logging

TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__), "org.freedesktop.Sdk.Extension.rust-nightly.yml"
)


class TestRustChecker(unittest.TestCase):
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
            r"^https://static.rust-lang.org/dist/[\-\d]+/rust-nightly-x86_64-unknown-linux-gnu.tar.xz$",
        )
        self.assertIsNone(data.new_version.size)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, str)
        self.assertNotEqual(
            data.new_version.checksum,
            "24b4681187654778817652273a68a4d55f5090604cd14b1f1c3ff8785ad24b99",
        )


if __name__ == "__main__":
    unittest.main()
