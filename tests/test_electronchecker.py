import os
import unittest
import datetime

from src.manifest import ManifestChecker
from src.lib.utils import init_logging
from src.lib.checksums import MultiDigest

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "fedc.test.ElectronChecker.yml")


class TestElectronChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()
        for data in ext_data:
            self.assertIsNotNone(data)
            self.assertIsNotNone(data.new_version)
            self.assertIsInstance(data.new_version.url, str)
            self.assertIsInstance(data.new_version.checksum, MultiDigest)
            self.assertIsInstance(data.new_version.size, int)
            self.assertIsInstance(data.new_version.version, str)
            self.assertIsInstance(data.new_version.timestamp, datetime.date)
            self.assertNotEqual(data.new_version.url, data.current_version.url)
