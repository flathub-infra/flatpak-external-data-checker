import os
import unittest

from src.checker import ManifestChecker
from src.lib.utils import init_logging

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "io.github.stedolan.jq.yml")


class TestJSONChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        self.assertEqual(len(ext_data), 2)
        for data in ext_data:
            self.assertIsNotNone(data)
            self.assertIsNotNone(data.new_version)
            if data.filename == "v6.9.4":
                url_re = (
                    r"^https://api.github.com/repos/kkos/oniguruma/tarball/v[0-9\.\w]+$"
                )
            elif data.filename == "jq-1.4.tar.gz":
                url_re = r"^https://github.com/stedolan/jq/releases/download/jq-[0-9\.\w]+/jq-[0-9\.\w]+\.tar.gz$"
            else:
                url_re = None
            self.assertIsNotNone(url_re)
            self.assertNotEqual(data.current_version.url, data.new_version.url)
            self.assertRegex(data.new_version.url, url_re)
            self.assertIsInstance(data.new_version.size, int)
            self.assertGreater(data.new_version.size, 0)
            self.assertIsNotNone(data.new_version.checksum)
            self.assertIsInstance(data.new_version.checksum, str)
            self.assertNotEqual(
                data.new_version.checksum,
                "0000000000000000000000000000000000000000000000000000000000000000",
            )
