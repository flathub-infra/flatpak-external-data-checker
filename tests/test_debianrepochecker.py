import datetime
import os
import unittest
from unittest import mock

import apt
import apt_pkg

from src.checkers.debianrepochecker import DebianRepoChecker, LoggerAcquireProgress
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


class TestDebianRepoCheckerMocked(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    def _make_external_data(self, component="main", dist="bookworm", arches=None):
        ed = mock.MagicMock()
        ed.checker_data = {
            "package-name": "apt",
            "root": "http://deb.debian.org/debian",
            "dist": dist,
            "component": component,
            "source": False,
        }
        ed.arches = arches or ["x86_64"]
        ed.filename = "apt-amd64.deb"
        return ed

    def test_file_like_flush_is_noop(self):
        logger = mock.MagicMock()
        progress = LoggerAcquireProgress(logger)
        progress._file.flush()

    async def test_check_returns_early(self):
        checker = DebianRepoChecker.__new__(DebianRepoChecker)
        checker.session = mock.AsyncMock()
        checker.robots_cache = mock.MagicMock()

        external_data = self._make_external_data(component="", dist="bookworm")

        with mock.patch.object(DebianRepoChecker, "should_check", return_value=True):
            with mock.patch.object(DebianRepoChecker, "_load_repo") as mock_load:
                await checker.check(external_data)
                mock_load.assert_not_called()

        external_data.set_new_version.assert_not_called()

    async def test_check_raises_missing_package(self):
        checker = DebianRepoChecker.__new__(DebianRepoChecker)
        checker.session = mock.AsyncMock()
        checker.robots_cache = mock.MagicMock()

        external_data = self._make_external_data()
        external_data.checker_data["source"] = True

        fake_cache = mock.MagicMock(spec=apt.Cache)
        src_record = mock.MagicMock(spec=apt_pkg.SourceRecords)
        src_record.lookup.return_value = False

        with mock.patch.object(DebianRepoChecker, "should_check", return_value=True):
            with mock.patch.object(
                DebianRepoChecker,
                "_load_repo",
                return_value=mock.MagicMock(
                    __enter__=mock.Mock(return_value=fake_cache),
                    __exit__=mock.Mock(return_value=False),
                ),
            ):
                with mock.patch("apt_pkg.SourceRecords", return_value=src_record):
                    with self.assertRaises(ValueError):
                        await checker.check(external_data)


if __name__ == "__main__":
    unittest.main()
