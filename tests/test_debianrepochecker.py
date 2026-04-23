import datetime
import os
import unittest
from unittest import mock

from src.lib.checksums import MultiDigest
from src.lib.utils import init_logging
from src.manifest import ManifestChecker

TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__), "org.debian.tracker.pkg.apt.yml"
)


class TestDebianRepoChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()
        for data in ext_data:
            self.assertIsNotNone(data)
            self.assertIsNotNone(data.new_version)
            self.assertIsNotNone(data.new_version.url)
            self.assertIsNotNone(data.new_version.checksum)
            self.assertIsNotNone(data.new_version.version)
            self.assertIsNotNone(data.new_version.timestamp)
            self.assertIsInstance(data.new_version.timestamp, datetime.date)
            self.assertNotEqual(data.new_version.url, data.current_version.url)
            self.assertIsInstance(data.new_version.checksum, MultiDigest)
            self.assertNotEqual(
                data.new_version.checksum, data.current_version.checksum
            )
            self.assertRegex(
                data.new_version.url, r"http://deb.debian.org/debian/pool/main/.+"
            )
            if data.filename == "python-apt-source.tar.xz":
                self.assertRegex(
                    data.new_version.url,
                    r"http://deb.debian.org/debian/pool/main/p/python-apt/python-apt_(\d[\d\.-]+\d).tar.xz",
                )
            elif data.filename == "apt-aarch64.deb":
                self.assertRegex(
                    data.new_version.url,
                    r"http://deb.debian.org/debian/pool/main/a/apt/apt_(\d[\d\.-]+\d)_arm64.deb",
                )
            else:
                self.fail(f"Unknown data {data.filename}")
