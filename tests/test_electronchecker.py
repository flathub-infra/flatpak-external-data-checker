import datetime
import os
import unittest
from unittest import mock

import aiohttp

from src.checkers.electronchecker import ElectronChecker
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerQueryError
from src.lib.externaldata import ExternalData
from src.lib.utils import init_logging
from src.manifest import ManifestChecker

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "fedc.test.ElectronChecker.yml")


class TestElectronChecker(unittest.IsolatedAsyncioTestCase):
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
            self.assertIsInstance(data.new_version.url, str)
            self.assertIsInstance(data.new_version.checksum, MultiDigest)
            self.assertIsInstance(data.new_version.size, int)
            self.assertIsInstance(data.new_version.version, str)
            self.assertIsInstance(data.new_version.timestamp, datetime.date)
            self.assertNotEqual(data.new_version.url, data.current_version.url)


class TestElectronCheckerMocked(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    def _make_checker(
        self, checker_data, current_url="https://example.com/app/latest-linux.yml"
    ):
        ext_data = mock.MagicMock(spec=ExternalData)
        ext_data.checker_data = checker_data
        ext_data.current_version = mock.MagicMock()
        ext_data.current_version.url = current_url
        checker = mock.MagicMock()
        checker.robots_cache = None
        checker.session = mock.MagicMock()
        return checker, ext_data

    async def _run_check(self, checker_obj, ext_data, resp_bytes):
        resp = mock.AsyncMock()
        resp.read = mock.AsyncMock(return_value=resp_bytes)
        resp.__aenter__ = mock.AsyncMock(return_value=resp)
        resp.__aexit__ = mock.AsyncMock(return_value=False)
        checker_obj.session.get.return_value = resp
        checker_obj._set_new_version = mock.AsyncMock()
        await ElectronChecker.check(checker_obj, ext_data)
        return checker_obj._set_new_version.call_args[0][1]

    async def test_url_from_current_version(self):
        checker_obj, ext_data = self._make_checker(
            checker_data={},
            current_url="https://example.com/app/app-1.0.0.AppImage",
        )
        yaml_bytes = b"""\
version: "2.0.0"
files:
  - url: app-2.0.0.AppImage
    size: 99999
    sha512: dGVzdA==
releaseDate: "2024-01-01T00:00:00.000Z"
"""
        nv = await self._run_check(checker_obj, ext_data, yaml_bytes)
        self.assertIn("example.com", nv.url)
        self.assertEqual(nv.version, "2.0.0")

    async def test_network_error(self):
        checker_obj, ext_data = self._make_checker(
            checker_data={"url": "https://example.com/latest-linux.yml"}
        )
        checker_obj.session.get.side_effect = aiohttp.ClientError("connection failed")
        checker_obj._set_new_version = mock.AsyncMock()
        with self.assertRaises(CheckerQueryError):
            await ElectronChecker.check(checker_obj, ext_data)

    async def test_old_format(self):
        checker_obj, ext_data = self._make_checker(
            checker_data={"url": "https://example.com/latest-linux.yml"}
        )
        yaml_bytes = b"""\
version: "1.0.0"
path: app-1.0.0.AppImage
sha512: dGVzdA==
releaseDate: "2023-06-01T00:00:00.000Z"
"""
        checker_obj._read_digests = ElectronChecker._read_digests
        nv = await self._run_check(checker_obj, ext_data, yaml_bytes)
        self.assertIsNone(nv.size)
        self.assertEqual(nv.version, "1.0.0")
        self.assertIsInstance(nv.checksum, MultiDigest)

    async def test_release_date_as_datetime(self):
        checker_obj, ext_data = self._make_checker(
            checker_data={"url": "https://example.com/latest-linux.yml"}
        )
        yaml_bytes = b"""\
version: "3.0.0"
files:
  - url: app-3.0.0.AppImage
    size: 12345
    sha512: dGVzdA==
releaseDate: 2024-03-15 12:00:00
"""
        nv = await self._run_check(checker_obj, ext_data, yaml_bytes)
        self.assertIsInstance(nv.timestamp, datetime.datetime)
        self.assertEqual(nv.version, "3.0.0")


if __name__ == "__main__":
    unittest.main()
