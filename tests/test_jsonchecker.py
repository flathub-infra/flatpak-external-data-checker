import os
import unittest
import datetime

from src.manifest import ManifestChecker
from src.lib.utils import init_logging
from src.lib.externaldata import ExternalFile, ExternalGitRef
from src.lib.checksums import MultiDigest

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "io.github.stedolan.jq.yml")


class TestJSONChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        self.assertEqual(len(ext_data), 8)
        for data in ext_data:
            self.assertIsNotNone(data)
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
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertNotEqual(
                    data.new_version.checksum,
                    MultiDigest(
                        sha256="0000000000000000000000000000000000000000000000000000000000000000"
                    ),
                )
            elif data.filename == "jq-1.4.tarball.tar.gz":
                self.assertEqual(
                    data.new_version.timestamp,
                    datetime.datetime.fromisoformat("2018-11-02T01:54:23+00:00"),
                )
            elif data.filename == "oniguruma.git":
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertEqual(data.current_version.url, data.new_version.url)
                self.assertIsNotNone(data.new_version.tag)
                self.assertIsNotNone(data.new_version.commit)
                self.assertNotEqual(data.new_version.tag, data.current_version.tag)
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
                self.assertNotEqual(
                    data.new_version.commit, "e03900b038a274ee2f1341039e9003875c11e47d"
                )
                self.assertIsNotNone(data.new_version.version)
                self.assertIsNotNone(data.new_version.timestamp)
            elif data.filename == "yasm.git":
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertEqual(data.current_version.url, data.new_version.url)
                self.assertIsNotNone(data.new_version.tag)
                self.assertIsNotNone(data.new_version.commit)
                self.assertNotEqual(data.new_version.tag, data.current_version.tag)
                self.assertIsNotNone(data.new_version.version)
                self.assertIsNone(data.new_version.timestamp)
            elif data.filename == "openal-soft.git":
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertEqual(data.current_version.url, data.new_version.url)
                self.assertIsNotNone(data.new_version.tag)
                self.assertIsNotNone(data.new_version.commit)
                self.assertIsNotNone(data.new_version.timestamp)
                self.assertIsInstance(data.new_version.timestamp, datetime.datetime)
            elif data.filename == "tdesktop.git":
                self.assertIsNone(data.new_version)
            elif data.filename == "lib_webrtc.git":
                self.assertIsNone(data.new_version)
            elif data.filename == "tg_angle.git":
                self.assertIsNone(data.new_version)
            else:
                self.fail(f"Unhandled data {data.filename}")
