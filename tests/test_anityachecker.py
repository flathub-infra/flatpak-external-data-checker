import os
import unittest
from datetime import datetime, timezone
from unittest import mock

import aiohttp

from src.checkers.anityachecker import AnityaChecker, _parse_timestamp
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerQueryError
from src.lib.externaldata import (
    ExternalData,
    ExternalFile,
    ExternalGitRef,
    ExternalGitRepo,
)
from src.lib.utils import init_logging
from src.lib.version import LooseVersion
from src.manifest import ManifestChecker

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.flatpak.Flatpak.yml")


class TestAnityaChecker(unittest.IsolatedAsyncioTestCase):
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

        self.assertEqual(len(ext_data), 6)
        for data in ext_data:
            if data.filename == "glib-networking-2.74.0.tar.xz":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertRegex(
                    data.new_version.url,
                    r"^https://download.gnome.org/sources/glib-networking/\d+.\d+/glib-networking-[\d.]+.tar.xz$",
                )
                self.assertIsNotNone(data.new_version.version)
                self.assertGreater(
                    LooseVersion(data.new_version.version), LooseVersion("2.76")
                )
                self.assertIsInstance(data.new_version.size, int)
                self.assertGreater(data.new_version.size, 0)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertNotEqual(
                    data.new_version.checksum,
                    MultiDigest(
                        sha256="1f185aaef094123f8e25d8fa55661b3fd71020163a0174adb35a37685cda613b",
                    ),
                )
                self.assertIsNotNone(data.new_version.timestamp)
            elif data.filename == "boost_1_74_0.tar.bz2":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertRegex(
                    data.new_version.url,
                    r"^https://archives\.boost\.io/release/[\d.]+/source/boost_[\d]+_[\d]+_[\d]+.tar.bz2$",
                )
                self.assertIsNotNone(data.new_version.version)
                self.assertGreater(
                    LooseVersion(data.new_version.version), LooseVersion("1.74.0")
                )
                self.assertIsInstance(data.new_version.size, int)
                self.assertGreater(data.new_version.size, 0)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertNotEqual(
                    data.new_version.checksum,
                    MultiDigest(
                        sha256="83bfc1507731a0906e387fc28b7ef5417d591429e51e788417fe9ff025e116b1"
                    ),
                )
                self.assertIsNotNone(data.new_version.timestamp)
            elif data.filename == "flatpak-1.8.2.tar.xz":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertRegex(
                    data.new_version.url,
                    r"^https://github.com/flatpak/flatpak/releases/download/[\w\d.]+/flatpak-[\w\d.]+.tar.xz$",
                )
                self.assertIsNotNone(data.new_version.version)
                self.assertEqual(
                    LooseVersion(data.new_version.version), LooseVersion("1.10.1")
                )
                self.assertIsInstance(data.new_version.size, int)
                self.assertGreater(data.new_version.size, 0)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertNotEqual(
                    data.new_version.checksum,
                    MultiDigest(
                        sha256="7926625df7c2282a5ee1a8b3c317af53d40a663b1bc6b18a2dc8747e265085b0"
                    ),
                )
                self.assertIsNotNone(data.new_version.timestamp)
            elif data.filename == "ostree.git":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertIsNotNone(data.new_version.commit)
                self.assertIsNotNone(data.new_version.tag)
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
                self.assertNotEqual(data.new_version.tag, data.current_version.tag)
                self.assertIsNotNone(data.new_version.version)
                self.assertGreater(
                    LooseVersion(data.new_version.version), LooseVersion("2020.7")
                )
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
                self.assertIsNotNone(data.new_version.timestamp)
            elif data.filename == "gr-iqbal.git":
                self.assertIsNone(data.new_version)
            elif data.filename == "yt-dlp.tar.gz":
                self.assertIsNotNone(data.new_version)
                self.assertIsInstance(data.new_version, ExternalFile)

                self.assertEqual(data.new_version.version, "2024.07.16")

                self.assertRegex(
                    data.new_version.url,
                    (
                        r"^https://github\.com/yt-dlp/yt-dlp/releases/download/"
                        r"2024\.07\.16/yt-dlp\.tar\.gz$"
                    ),
                )

                self.assertIsInstance(data.new_version.size, int)
                self.assertGreater(data.new_version.size, 0)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertIsNotNone(data.new_version.timestamp)
            else:
                self.fail(f"Unknown data {data.filename}")


_FLATPAK_ANITYA_RESPONSE = {
    "latest_version": "1.18.0",
    "latest_version_created_on": "2026-06-08T18:54:44.176150",
    "stable_versions": ["1.18.0", "1.16.0", "1.14.0", "1.12.0", "1.10.1", "1.8.2"],
    "versions": ["1.18.0", "1.15.91", "1.16.0", "1.14.0", "1.12.0", "1.10.1", "1.8.2"],
}

_FAKE_ARCHIVE = ExternalFile(
    url="https://example.invalid/fake-1.0.tar.xz",
    checksum=MultiDigest(sha256="a" * 64),
    size=1234,
    version=None,
    timestamp=None,
)


class TestAnityaCheckerMocked(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    async def _fake_fetch(url, **kwargs):
        return _FAKE_ARCHIVE._replace(url=str(url))

    def setUp(self):
        init_logging()

        self.checker = AnityaChecker(session=mock.MagicMock())

        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed",
            new_callable=mock.AsyncMock,
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

        response_mock = mock.AsyncMock()
        response_mock.json = mock.AsyncMock(return_value=_FLATPAK_ANITYA_RESPONSE)
        cm = mock.AsyncMock()
        cm.__aenter__ = mock.AsyncMock(return_value=response_mock)
        cm.__aexit__ = mock.AsyncMock(return_value=False)
        self.session_patcher = mock.patch(
            "aiohttp.ClientSession.get",
            return_value=cm,
        )
        self.session_patcher.start()
        self.addCleanup(self.session_patcher.stop)

        self.fetch_patcher = mock.patch(
            "src.lib.utils.get_extra_data_info_from_url",
            side_effect=self._fake_fetch,
        )
        self.fetch_patcher.start()
        self.addCleanup(self.fetch_patcher.stop)

    def _make_external_data(self, checker_data):
        ed = mock.MagicMock(spec=ExternalData)
        ed.checker_data = checker_data
        ed.checker_data["type"] = "anitya"
        return ed

    def _make_external_git_repo(self, checker_data):
        eg = mock.MagicMock(spec=ExternalGitRepo)
        eg.checker_data = checker_data
        eg.checker_data["type"] = "anitya"
        eg.current_version = mock.MagicMock()
        eg.current_version.url = "https://github.com/example/repo.git"
        return eg

    def _mock_session_response(self, payload):
        resp = mock.AsyncMock()
        resp.json = mock.AsyncMock(return_value=payload)
        resp.__aenter__ = mock.AsyncMock(return_value=resp)
        resp.__aexit__ = mock.AsyncMock(return_value=False)
        session = mock.MagicMock()
        session.get = mock.MagicMock(return_value=resp)
        self.checker.session = session

    async def test_network_error_on_raises(self):
        resp = mock.AsyncMock()
        resp.json = mock.AsyncMock(side_effect=aiohttp.ClientError("boom"))
        resp.__aenter__ = mock.AsyncMock(return_value=resp)
        resp.__aexit__ = mock.AsyncMock(return_value=False)
        session = mock.MagicMock()
        session.get = mock.MagicMock(return_value=resp)
        self.checker.session = session

        ed = self._make_external_data(
            {"project-id": 1, "url-template": "https://example.com/{version}.tar.gz"}
        )

        with self.assertRaises(CheckerQueryError):
            await self.checker.check(ed)

    async def test_no_constraints_uses_latest(self):
        payload = {
            "latest_version": "3.0.0",
            "stable_versions": ["2.0.0"],
            "versions": ["3.0.0-alpha", "2.0.0"],
        }
        self._mock_session_response(payload)

        ed = self._make_external_data(
            {
                "project-id": 42,
                "stable-only": False,
                "url-template": "https://example.com/{version}.tar.gz",
            }
        )

        self.checker._check_data = mock.AsyncMock()
        await self.checker.check(ed)

        self.checker._check_data.assert_awaited_once_with(ed, "3.0.0", None)

    async def test_stable_only_selects_first_stable(self):
        payload = {
            "latest_version": "3.0.0-alpha",
            "stable_versions": ["2.0.0", "1.9.0"],
            "versions": ["3.0.0-alpha", "2.0.0", "1.9.0"],
        }
        self._mock_session_response(payload)

        ed = self._make_external_data(
            {
                "project-id": 42,
                "url-template": "https://example.com/{version}.tar.gz",
            }
        )

        self.checker._check_data = mock.AsyncMock()
        await self.checker.check(ed)

        self.checker._check_data.assert_awaited_once_with(ed, "2.0.0", None)

    async def test_filter_no_match_raises(self):

        payload = {
            "latest_version": "3.0.0",
            "stable_versions": ["3.0.0", "2.0.0"],
            "versions": ["3.0.0", "2.0.0"],
        }
        self._mock_session_response(payload)

        ed = self._make_external_data(
            {
                "project-id": 42,
                "stable-only": False,
                "versions": {"<": "1.0.0"},
                "url-template": "https://example.com/{version}.tar.gz",
            }
        )

        with self.assertRaises(CheckerQueryError):
            await self.checker.check(ed)

    async def test_external_git_repo_dispatches_to_check_git(self):
        payload = {
            "latest_version": "2023.1",
            "stable_versions": ["2023.1"],
            "versions": ["2023.1"],
        }
        self._mock_session_response(payload)

        eg = self._make_external_git_repo(
            {
                "project-id": 99,
                "tag-template": "v{version}",
            }
        )

        self.checker._check_git = mock.AsyncMock()
        await self.checker.check(eg)

        self.checker._check_git.assert_awaited_once_with(eg, "2023.1", None)

    async def test_flatpak_timestamp(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        flatpak = next(d for d in ext_data if d.filename == "flatpak-1.8.2.tar.xz")

        self.assertIsNotNone(flatpak.new_version)
        self.assertIsInstance(flatpak.new_version, ExternalFile)

        self.assertEqual(flatpak.new_version.version, "1.10.1")
        self.assertEqual(
            flatpak.new_version.url,
            "https://github.com/flatpak/flatpak/releases/download/1.10.1/flatpak-1.10.1.tar.xz",
        )

        self.assertIsNotNone(flatpak.new_version.timestamp)
        self.assertEqual(
            flatpak.new_version.timestamp,
            datetime(2026, 6, 8, 18, 54, 44, 176150, tzinfo=timezone.utc),
        )


class TestParseTimestamp(unittest.TestCase):
    def test_invalid(self):
        with self.assertRaises(CheckerQueryError):
            _parse_timestamp("not-a-date")

    def test_no_tz(self):
        self.assertEqual(
            _parse_timestamp("2026-06-08T18:54:44.176150"),
            datetime(2026, 6, 8, 18, 54, 44, 176150, tzinfo=timezone.utc),
        )

    def test_z_suffix(self):
        self.assertEqual(
            _parse_timestamp("2022-07-15T10:00:00Z"),
            datetime(2022, 7, 15, 10, 0, 0, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
