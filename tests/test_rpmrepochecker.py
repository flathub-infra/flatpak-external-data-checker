import unittest
import os

from src.manifest import ManifestChecker
from src.lib.utils import init_logging
from src.lib.checksums import MultiDigest

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.visualstudio.code.yaml")


class TestRPMRepoChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()
        for data in ext_data:
            self.assertIsNotNone(data)
            self.assertIsNotNone(data.new_version)
            self.assertIsNotNone(data.new_version.url)
            self.assertIsNotNone(data.new_version.checksum)
            self.assertIsNotNone(data.new_version.version)
            self.assertNotEqual(data.new_version.url, data.current_version.url)
            self.assertIsInstance(data.new_version.checksum, MultiDigest)
            self.assertNotEqual(
                data.new_version.checksum, data.current_version.checksum
            )
            self.assertRegex(
                data.new_version.url,
                r"https://packages\.microsoft\.com/yumrepos/vscode/code-.+\.{0}\.rpm".format(
                    data.arches[0]
                ),
            )
