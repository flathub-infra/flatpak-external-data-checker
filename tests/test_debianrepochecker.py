import unittest
import os

from src.checker import ManifestChecker
from src.lib.utils import init_logging

TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__), "org.debian.tracker.pkg.apt.yml"
)


class TestDebianRepoChecker(unittest.IsolatedAsyncioTestCase):
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
