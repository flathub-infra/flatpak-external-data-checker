import os
import unittest

from src.checker import ManifestChecker
from src.lib.utils import init_logging
from src.lib.externaldata import ExternalFile, ExternalGitRef

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
            if data.filename == "jq-1.4.tar.gz":
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertNotEqual(data.current_version.url, data.new_version.url)
                self.assertRegex(
                    data.new_version.url,
                    r"^https://github.com/stedolan/jq/releases/download/jq-[0-9\.\w]+/jq-[0-9\.\w]+\.tar.gz$",
                )
                self.assertIsInstance(data.new_version.size, int)
                self.assertGreater(data.new_version.size, 0)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, str)
                self.assertNotEqual(
                    data.new_version.checksum,
                    "0000000000000000000000000000000000000000000000000000000000000000",
                )
            elif data.filename == "oniguruma.git":
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertEqual(data.current_version.url, data.new_version.url)
                self.assertIsNotNone(data.new_version.tag)
                self.assertIsNotNone(data.new_version.commit)
                self.assertNotEqual(
                    data.new_version.commit, "e03900b038a274ee2f1341039e9003875c11e47d"
                )
                self.assertIsNotNone(data.new_version.version)
            else:
                self.fail(f"Unhandled data {data.filename}")
