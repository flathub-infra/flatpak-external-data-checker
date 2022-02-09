import os
import unittest

from src.manifest import ManifestChecker
from src.lib.utils import init_logging
from src.lib.checksums import MultiDigest

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.nordpass.NordPass.yaml")


class TestSnapctaftChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        data = self._find_by_filename(ext_data, "nordpass.snap")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "nordpass.snap")
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https://api\.snapcraft\.io/api/v1/snaps/download/[A-Za-z0-9]{32}_[0-9]+.snap$",
        )
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

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        else:
            return None


if __name__ == "__main__":
    unittest.main()
