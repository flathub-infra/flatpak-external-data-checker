import base64
import unittest
from unittest import mock

import aiohttp
from yarl import URL

from src.checkers.anityachecker import AnityaChecker
from src.checkers.chromiumchecker import ChromiumChecker, LLVMComponent
from src.checkers.electronchecker import ElectronChecker
from src.checkers.gnomechecker import GNOMEChecker
from src.checkers.htmlchecker import HTMLChecker
from src.checkers.jetbrainschecker import JetBrainsChecker
from src.checkers.jsonchecker import JSONChecker
from src.checkers.pypichecker import PyPIChecker
from src.checkers.rustchecker import RustChecker
from src.checkers.snapcraftchecker import SnapcraftChecker
from src.checkers.urlchecker import URLChecker
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerFetchError, CheckerMetadataError
from src.lib.externaldata import (
    ExternalData,
    ExternalFile,
    ExternalGitRepo,
)
from src.lib.robots import RobotsCache
from src.lib.utils import get_extra_data_info_from_url, get_timestamp_from_url
from src.manifest import CheckerOptions, ManifestChecker


def _make_external_data(url, checker_type, extra_checker_data=None, enable_robots=True):
    checker_data = {"type": checker_type, **(extra_checker_data or {})}

    if enable_robots is not None:
        checker_data["enable-robots-txt"] = enable_robots

    source = {
        "type": "extra-data",
        "filename": "test.tar.gz",
        "url": url,
        "sha256": "0" * 64,
        "size": 0,
        "x-checker-data": checker_data,
    }
    return ExternalData.from_source("test.yaml", source)


def _make_git_repo(url, checker_type, extra_checker_data=None, enable_robots=True):
    checker_data = {"type": checker_type, **(extra_checker_data or {})}

    if enable_robots is not None:
        checker_data["enable-robots-txt"] = enable_robots

    source = {
        "type": "git",
        "url": url,
        "tag": "v1.0",
        "commit": "a" * 40,
        "x-checker-data": checker_data,
    }
    return ExternalGitRepo.from_source("test.yaml", source)


def _make_robots_cache(blocked=False):
    cache = mock.AsyncMock(spec=RobotsCache)
    if blocked:
        cache.ensure_allowed.side_effect = CheckerFetchError("Blocked by robots.txt")
    else:
        cache.ensure_allowed.return_value = None
    return cache


def _make_mock_resp(content_bytes=b"", headers=None, url=None):
    mock_resp = mock.MagicMock()
    mock_resp.headers = headers or {}
    if url:
        mock_resp.url = url

    async def async_iter_chunks():
        yield content_bytes, True

    async def async_iter_chunked(size):
        yield content_bytes

    mock_resp.content.iter_chunks = async_iter_chunks
    mock_resp.content.iter_chunked = async_iter_chunked
    return mock_resp


class TestBaseCheckerRobotsCoverage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)
        self.robots_cache = mock.AsyncMock(spec=RobotsCache)
        self.checker = HTMLChecker(self.session, self.robots_cache)

    async def test_get_xml_robots(self):
        url = URL("https://example.com/manifest.xml")
        mock_resp = _make_mock_resp(b"<root></root>")

        with mock.patch.object(self.session, "get") as mock_get:
            mock_get.return_value.__aenter__.return_value = mock_resp
            await self.checker._get_xml(url)

        self.robots_cache.ensure_allowed.assert_called_with(url)

    async def test_complete_digests_robots(self):
        url = "https://example.com/file.tar.gz"
        digests = MultiDigest(sha256="0" * 64)
        mock_resp = _make_mock_resp(b"data")

        with (
            mock.patch.object(self.session, "get") as mock_get,
            mock.patch("src.checkers.MultiHash.hexdigest", return_value=digests),
        ):
            mock_get.return_value.__aenter__.return_value = mock_resp
            await self.checker._complete_digests(url, digests)

        self.robots_cache.ensure_allowed.assert_called_with(url)


class TestURLCheckerRobots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    async def test_ensure_allowed_called_before_request(self):
        url = "http://example.com/last-version"
        data = _make_external_data(url, "rotating-url", {"url": url})
        robots = _make_robots_cache()
        checker = URLChecker(self.session, robots)

        with mock.patch(
            "src.checkers.urlchecker.utils.get_extra_data_info_from_url",
            new=mock.AsyncMock(
                return_value=ExternalFile(
                    url=url,
                    checksum=MultiDigest(sha256="0" * 64),
                    size=0,
                    version=None,
                    timestamp=None,
                )
            ),
        ) as mock_get_info:
            await checker.check(data)

        mock_get_info.assert_called_once()
        self.assertEqual(mock_get_info.call_args.kwargs.get("robots_cache"), robots)

    async def test_blocked_raises_before_request(self):
        url = "http://example.com/last-version"
        data = _make_external_data(url, "rotating-url", {"url": url})
        robots = _make_robots_cache(blocked=True)
        checker = URLChecker(self.session, robots)

        with mock.patch(
            "src.checkers.urlchecker.utils.get_extra_data_info_from_url",
            new=mock.AsyncMock(side_effect=CheckerFetchError("Blocked by robots.txt")),
        ):
            with self.assertRaises(CheckerFetchError):
                await checker.check(data)

        self.session.get.assert_not_called()
        self.session.head.assert_not_called()

    async def test_ensure_allowed_called_with_strip_query(self):
        url = "https://example.com/file?token=123"
        data = _make_external_data(
            url, "rotating-url", {"url": url, "strip-query": True}
        )
        robots = _make_robots_cache()
        checker = URLChecker(self.session, robots)

        mock_head_resp = mock.AsyncMock()
        mock_head_resp.url = URL(url)

        mock_get_info = mock.AsyncMock(
            return_value=ExternalFile(
                url=url,
                checksum=MultiDigest(sha256="0" * 64),
                size=0,
                version="1.0",
                timestamp=None,
            )
        )

        with (
            mock.patch.object(self.session, "head") as mock_head,
            mock.patch(
                "src.checkers.urlchecker.utils.get_extra_data_info_from_url",
                new=mock_get_info,
            ),
        ):
            mock_head.return_value.__aenter__.return_value = mock_head_resp
            await checker.check(data)

        robots.ensure_allowed.assert_any_call(url)


class TestHTMLCheckerRobots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    async def test_ensure_allowed_called_before_fetch(self):
        url = "https://example.com/releases/"
        data = _make_external_data(
            "https://example.com/file-1.0.tar.gz",
            "html",
            {
                "url": url,
                "version-pattern": r"file-([\d.]+)\.tar\.gz",
                "url-template": "https://example.com/file-$version.tar.gz",
            },
        )
        robots = _make_robots_cache()
        checker = HTMLChecker(self.session, robots)

        async def dummy_iter_chunks(*args, **kwargs):
            yield b"file-2.0.tar.gz", b""

        mock_response = mock.AsyncMock()
        mock_response.headers = {"Content-Type": "text/html; charset=utf-8"}
        mock_response.content.iter_chunks = dummy_iter_chunks

        with (
            mock.patch.object(
                self.session,
                "get",
                return_value=mock.AsyncMock(
                    __aenter__=mock.AsyncMock(return_value=mock_response),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
            ),
            mock.patch(
                "src.lib.utils.get_extra_data_info_from_url",
                new=mock.AsyncMock(
                    return_value=ExternalFile(
                        url="https://example.com/file-2.0.tar.gz",
                        checksum=MultiDigest(sha256="0" * 64),
                        size=0,
                        version=None,
                        timestamp=None,
                    )
                ),
            ) as mock_get_info,
        ):
            await checker.check(data)

        robots.ensure_allowed.assert_called_once_with(url)
        self.assertEqual(mock_get_info.call_args.kwargs.get("robots_cache"), robots)

    async def test_blocked_raises_before_fetch(self):
        url = "https://example.com/releases/"
        data = _make_external_data(
            "https://example.com/file-1.0.tar.gz",
            "html",
            {
                "url": url,
                "version-pattern": r"file-([\d.]+)\.tar\.gz",
                "url-template": "https://example.com/file-$version.tar.gz",
            },
        )
        robots = _make_robots_cache(blocked=True)
        checker = HTMLChecker(self.session, robots)

        with self.assertRaises(CheckerFetchError):
            await checker.check(data)

        self.session.get.assert_not_called()


class TestJSONCheckerRobots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    async def test_ensure_allowed_called_before_fetch(self):
        api_url = "https://example.com/api/releases/latest"
        data = _make_external_data(
            "https://example.com/file-1.0.tar.gz",
            "json",
            {
                "url": api_url,
                "version-query": ".version",
                "url-query": ".url",
            },
        )
        robots = _make_robots_cache()
        checker = JSONChecker(self.session, robots)

        mock_url = mock.Mock()
        mock_url.name = "dummy.json"

        mock_response = mock.AsyncMock()
        mock_response.json.return_value = {
            "version": "2.0",
            "url": "https://example.com/file-2.0.tar.gz",
        }
        mock_response.url = mock_url

        with (
            mock.patch.object(
                self.session,
                "get",
                return_value=mock.AsyncMock(
                    __aenter__=mock.AsyncMock(return_value=mock_response),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
            ),
            mock.patch(
                "src.lib.utils.get_extra_data_info_from_url",
                new=mock.AsyncMock(
                    return_value=ExternalFile(
                        url="https://example.com/file-2.0.tar.gz",
                        checksum=MultiDigest(sha256="0" * 64),
                        size=0,
                        version=None,
                        timestamp=None,
                    )
                ),
            ) as mock_get_info,
        ):
            await checker.check(data)

        robots.ensure_allowed.assert_called_once()
        called_url = str(robots.ensure_allowed.call_args[0][0])
        self.assertEqual(called_url, api_url)
        self.assertEqual(mock_get_info.call_args.kwargs.get("robots_cache"), robots)

    async def test_blocked_raises_before_fetch(self):
        api_url = "https://example.com/api/releases/latest"
        data = _make_external_data(
            "https://example.com/file-1.0.tar.gz",
            "json",
            {
                "url": api_url,
                "version-query": ".version",
                "url-query": ".url",
            },
        )
        robots = _make_robots_cache(blocked=True)
        checker = JSONChecker(self.session, robots)

        with self.assertRaises(CheckerFetchError):
            await checker.check(data)

        self.session.get.assert_not_called()


class TestAnityaCheckerRobots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    async def test_ensure_allowed_called_before_fetch(self):
        data = _make_external_data(
            "https://example.com/file-1.0.tar.gz",
            "anitya",
            {
                "project-id": 1234,
                "url-template": "https://example.com/file-$version.tar.gz",
            },
        )
        robots = _make_robots_cache()
        checker = AnityaChecker(self.session, robots)

        with (
            mock.patch.object(checker, "_update_version", new=mock.AsyncMock()),
            mock.patch.object(
                self.session,
                "get",
                return_value=mock.AsyncMock(
                    __aenter__=mock.AsyncMock(
                        return_value=mock.AsyncMock(
                            json=mock.AsyncMock(
                                return_value={
                                    "latest_version": "2.0",
                                    "stable_versions": ["2.0"],
                                    "versions": ["2.0"],
                                }
                            )
                        )
                    ),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
            ),
        ):
            await checker.check(data)

        robots.ensure_allowed.assert_called_once()
        called_url = str(robots.ensure_allowed.call_args[0][0])
        self.assertIn("release-monitoring.org", called_url)
        self.assertIn("1234", called_url)

    async def test_blocked_raises_before_fetch(self):
        data = _make_external_data(
            "https://example.com/file-1.0.tar.gz",
            "anitya",
            {
                "project-id": 1234,
                "url-template": "https://example.com/file-$version.tar.gz",
            },
        )
        robots = _make_robots_cache(blocked=True)
        checker = AnityaChecker(self.session, robots)

        with self.assertRaises(CheckerFetchError):
            await checker.check(data)

        self.session.get.assert_not_called()


class TestJetBrainsCheckerRobots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    async def test_ensure_allowed_called_for_api_and_checksum(self):
        data = _make_external_data(
            "https://download.jetbrains.com/idea/idea-2026.1.1.tar.gz",
            "jetbrains",
            {"code": "IIU"},
        )
        data.arches = ["x86_64"]

        robots = _make_robots_cache()
        checker = JetBrainsChecker(self.session, robots)

        checksum_url = "https://download.jetbrains.com/idea/idea-2026.1.1.tar.gz.sha256"

        release_data = {
            "IIU": [
                {
                    "date": "2024-01-01",
                    "type": "release",
                    "downloads": {
                        "linux": {
                            "link": "https://download.jetbrains.com/idea/idea-2026.1.1.tar.gz",
                            "size": 1566343844,
                            "checksumLink": checksum_url,
                        },
                    },
                    "version": "2026.1.1",
                }
            ]
        }

        with mock.patch.object(
            self.session,
            "get",
            side_effect=[
                mock.AsyncMock(
                    __aenter__=mock.AsyncMock(
                        return_value=mock.AsyncMock(
                            json=mock.AsyncMock(return_value=release_data)
                        )
                    ),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
                mock.AsyncMock(
                    __aenter__=mock.AsyncMock(
                        return_value=mock.AsyncMock(
                            text=mock.AsyncMock(return_value="abcd1234 file.tar.gz")
                        )
                    ),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
            ],
        ):
            await checker.check(data)

        ensure_calls = [str(c[0][0]) for c in robots.ensure_allowed.call_args_list]
        self.assertTrue(any("jetbrains.com" in u for u in ensure_calls))
        self.assertTrue(any(checksum_url in u for u in ensure_calls))

    async def test_blocked_raises_before_api_call(self):
        data = _make_external_data(
            "https://download.jetbrains.com/idea/ideaIC-2023.1.tar.gz",
            "jetbrains",
            {"code": "IC"},
        )
        robots = _make_robots_cache(blocked=True)
        checker = JetBrainsChecker(self.session, robots)

        with self.assertRaises(CheckerFetchError):
            await checker.check(data)

        self.session.get.assert_not_called()


class TestRustCheckerRobots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    async def test_ensure_allowed_called_before_fetch(self):
        data = _make_external_data(
            "https://static.rust-lang.org/dist/rust-1.70.0-x86_64-unknown-linux-gnu.tar.gz",
            "rust",
            {
                "package": "rust",
                "target": "x86_64-unknown-linux-gnu",
                "channel": "stable",
            },
        )
        robots = _make_robots_cache()
        checker = RustChecker(self.session)
        checker.robots_cache = robots

        toml_data = """
manifest-version = "2"
date = "2026-04-16"
[pkg.rust]
version = "1.95.0 (59807616e 2026-04-14)"
git_commit_hash = "59807616e1fa2540724bfbac14d7976d7e4a3860" # noqa: E501
[pkg.rust.target.x86_64-unknown-linux-gnu]
available = true
url = "https://static.rust-lang.org/dist/2026-04-16/rust-1.95.0-x86_64-unknown-linux-gnu.tar.gz"
hash = "a47ac940abd12399d59ad15c877e7113fa35f2b9ec7e6a8a045d4fd8b9741dea"
xz_url = "https://static.rust-lang.org/dist/2026-04-16/rust-1.95.0-x86_64-unknown-linux-gnu.tar.xz"
xz_hash = "2e0338f18ecbaa4a0f631b9e80e8b8e26bb6fe77dd5454fba8a70cf96c1e84a1"
"""
        with mock.patch.object(
            self.session,
            "get",
            return_value=mock.AsyncMock(
                __aenter__=mock.AsyncMock(
                    return_value=mock.AsyncMock(
                        text=mock.AsyncMock(return_value=toml_data)
                    )
                ),
                __aexit__=mock.AsyncMock(return_value=False),
            ),
        ):
            await checker.check(data)

        robots.ensure_allowed.assert_called_once()
        called_url = str(robots.ensure_allowed.call_args[0][0])
        self.assertIn("static.rust-lang.org", called_url)
        self.assertIn("stable", called_url)

    async def test_blocked_raises_before_fetch(self):
        data = _make_external_data(
            "https://static.rust-lang.org/dist/rust-1.70.0-x86_64-unknown-linux-gnu.tar.gz",
            "rust",
            {
                "package": "rust",
                "target": "x86_64-unknown-linux-gnu",
                "channel": "stable",
            },
        )
        robots = _make_robots_cache(blocked=True)
        checker = RustChecker(self.session)
        checker.robots_cache = robots

        with self.assertRaises(CheckerFetchError):
            await checker.check(data)

        self.session.get.assert_not_called()


class TestSnapcraftCheckerRobots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    def test_init(self):
        checker_pos = SnapcraftChecker(self.session, mock.Mock())
        self.assertIsNone(checker_pos.robots_cache)

        checker_kw = SnapcraftChecker(self.session)
        self.assertIsNone(checker_kw.robots_cache)

    async def test_ensure_allowed_called_before_fetch(self):
        data = _make_external_data(
            "https://api.snapcraft.io/api/v1/snaps/download/old_snap_shaabcd.snap",
            "snapcraft",
            {"name": "slack", "channel": "stable"},
        )
        data.arches = ["x86_64"]

        robots = _make_robots_cache()
        checker = SnapcraftChecker(self.session)
        checker.robots_cache = robots

        snap_info = {
            "channel-map": [
                {
                    "channel": {
                        "architecture": "amd64",
                        "name": "stable",
                        "released-at": "2026-04-14T16:57:40.460707+00:00",
                        "risk": "stable",
                        "track": "latest",
                    },
                    "download": {
                        "deltas": [],
                        "sha3-384": "581d9ae3142e39750f74ac4e9a736dac6d725c1290efdf30e331a77bbbd5c1e8ca82b0882e6aff7573b6986a9eb3364b",  # noqa: E501
                        "size": 134029312,
                        "url": "https://api.snapcraft.io/api/v1/snaps/download/JUJH91Ved74jd4ZgJCpzMBtYbPOzTlsD_224.snap",
                    },
                    "version": "4.47.69",
                }
            ]
        }

        mock_info_resp = mock.AsyncMock()
        mock_info_resp.json.return_value = snap_info
        mock_blob_resp = _make_mock_resp(b"binary-data")

        with mock.patch.object(self.session, "get") as mock_get:
            mock_get.side_effect = [
                mock.AsyncMock(
                    __aenter__=mock.AsyncMock(return_value=mock_info_resp),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
                mock.AsyncMock(
                    __aenter__=mock.AsyncMock(return_value=mock_blob_resp),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
            ]
            await checker.check(data)

        self.assertEqual(robots.ensure_allowed.call_count, 2)


class TestElectronCheckerRobots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    async def test_ensure_allowed_called_before_fetch(self):
        metadata_url = "https://github.com/Vencord/Vesktop/releases/latest/download/latest-linux.yml"
        data = _make_external_data(
            "https://github.com/Vencord/Vesktop/releases/latest/download/Vesktop-1.6.5.AppImage",
            "electron-updater",
            {"url": metadata_url},
        )
        robots = _make_robots_cache()
        checker = ElectronChecker(self.session, robots)

        yaml_content = b"""
version: 1.6.5
files:
  - url: Vesktop-1.6.5.AppImage
    sha512: zLOzJf4/h9xAsLOpyA4ouElIw87sgUdOYnkn3YYCdhba4PAealAljigZHn0NI9uSM2PqNf8E+50rjK+YmRsrLg==
    size: 122382283
    blockMapSize: 128991
  - url: vesktop_1.6.5_amd64.deb
    sha512: PwicVlmjEkss5+t//Cx0azpFDm1BRGFtmb0oQM9iRVjrc3EwK5QYmYgu1GODrDoyEMwzb5n752sFpOdteAcMbA==
    size: 96177720
  - url: vesktop-1.6.5.x86_64.rpm
    sha512: 73cI/2z3BCNiVXTLlN4kyyIJtAXLpiYcgR6QZDyrGy7cZgkOtCO8RxQUDBZ9LYOtCPBj16xZiQGQW7VmVYhVcw==
    size: 84288653
path: Vesktop-1.6.5.AppImage
sha512: zLOzJf4/h9xAsLOpyA4ouElIw87sgUdOYnkn3YYCdhba4PAealAljigZHn0NI9uSM2PqNf8E+50rjK+YmRsrLg==
releaseDate: '2026-02-12T04:17:00.167Z'
"""  # noqa: E501
        with (
            mock.patch.object(
                self.session,
                "get",
                return_value=mock.AsyncMock(
                    __aenter__=mock.AsyncMock(
                        return_value=mock.AsyncMock(
                            read=mock.AsyncMock(return_value=yaml_content)
                        )
                    ),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
            ),
            mock.patch(
                "src.checkers.electronchecker.ElectronChecker._set_new_version",
                new=mock.AsyncMock(),
            ),
        ):
            await checker.check(data)

        robots.ensure_allowed.assert_called_once()
        called_url = str(robots.ensure_allowed.call_args[0][0])
        self.assertIn("latest-linux.yml", called_url)

    async def test_blocked_raises_before_fetch(self):
        metadata_url = "https://github.com/Vencord/Vesktop/releases/latest/download/latest-linux.yml"
        data = _make_external_data(
            "https://github.com/Vencord/Vesktop/releases/latest/download/Vesktop-1.6.5.AppImage",
            "electron-updater",
            {"url": metadata_url},
        )
        robots = _make_robots_cache(blocked=True)
        checker = ElectronChecker(self.session, robots)

        with self.assertRaises(CheckerFetchError):
            await checker.check(data)

        self.session.get.assert_not_called()


class TestGNOMECheckerRobots(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    async def test_ensure_allowed_called_for_cache_and_checksum(self):
        data = _make_external_data(
            "https://download.gnome.org/sources/baobab/3.34/baobab-3.34.0.tar.xz",
            "gnome",
            {"name": "baobab"},
        )
        robots = _make_robots_cache()
        checker = GNOMEChecker(self.session, robots)

        cache_json = [
            4,
            {
                "baobab": {
                    "3.34.1": {
                        "tar.xz": "3.34/baobab-3.34.1.tar.xz",
                        "sha256sum": "3.34/baobab-3.34.1.sha256sum",
                    },
                    "3.34.0": {
                        "tar.xz": "3.34/baobab-3.34.0.tar.xz",
                        "sha256sum": "3.34/baobab-3.34.0.sha256sum",
                    },
                }
            },
            {"baobab": ["3.34.0", "3.34.1"]},
            {},
        ]

        with (
            mock.patch.object(self.session, "get") as mock_get,
            mock.patch(
                "src.checkers.gnomechecker._parse_checksums",
                return_value={
                    "baobab-3.34.1.tar.xz": "46ebd9466da6a68c340653e9095f1e905b6fac79305879a9e644634f7da98607"  # noqa: E501
                },
            ),
        ):
            mock_cache_resp = mock.AsyncMock()
            mock_cache_resp.json.return_value = cache_json

            mock_cs_resp = mock.AsyncMock()
            mock_cs_resp.text.return_value = "46ebd9466da6a68c340653e9095f1e905b6fac79305879a9e644634f7da98607  baobab-3.34.1.tar.xz"  # noqa: E501

            mock_get.side_effect = [
                mock.AsyncMock(
                    __aenter__=mock.AsyncMock(return_value=mock_cache_resp),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
                mock.AsyncMock(
                    __aenter__=mock.AsyncMock(return_value=mock_cs_resp),
                    __aexit__=mock.AsyncMock(return_value=False),
                ),
            ]

            await checker.check(data)

        self.assertEqual(robots.ensure_allowed.call_count, 2)
        ensure_calls = [str(c[0][0]) for c in robots.ensure_allowed.call_args_list]
        self.assertTrue(any("cache.json" in u for u in ensure_calls))
        self.assertTrue(any("baobab-3.34.1.sha256sum" in u for u in ensure_calls))


class TestChromiumRobotsCoverage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)
        self.robots_cache = mock.AsyncMock(spec=RobotsCache)

    async def test_get_latest_chromium_robots_call(self):
        checker = ChromiumChecker(self.session, self.robots_cache)

        mock_response = mock.AsyncMock()
        mock_response.json.return_value = [{"version": "124.0.0.0"}]
        self.session.get.return_value.__aenter__.return_value = mock_response

        await checker._get_latest_chromium()
        self.robots_cache.ensure_allowed.assert_called_with(
            checker._CHROMIUM_VERSIONS_URL
        )

    async def test_get_llvm_version_robots_call(self):
        component = LLVMComponent(self.session, mock.Mock(), "124.0.0.0")
        component.robots_cache = self.robots_cache

        raw_text = b"CLANG_REVISION = '1234'\nCLANG_SUB_REVISION = 1"
        mock_response = mock.AsyncMock()
        mock_response.text.return_value = base64.b64encode(raw_text).decode()
        self.session.get.return_value.__aenter__.return_value = mock_response

        await component.get_llvm_version()
        self.robots_cache.ensure_allowed.assert_called()

    async def test_checker_check_metadata_error(self):
        data = _make_external_data("https://example.com", "chromium")
        data.checker_data["component"] = "invalid-component-name"

        checker = ChromiumChecker(self.session)

        class MockComponent:
            DATA_CLASS = str

        with (
            mock.patch.object(
                ChromiumChecker,
                "_COMPONENTS",
                {"invalid-component-name": MockComponent},
            ),
            self.assertRaises(CheckerMetadataError),
        ):
            await checker.check(data)


class TestPyPICheckerInitCoverage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)

    def test_init(self):
        checker = PyPIChecker(self.session)
        self.assertIsNone(checker.robots_cache)

        checker_kw = PyPIChecker(self.session, robots_cache=mock.Mock())
        self.assertIsNone(checker_kw.robots_cache)


class TestRobotsManifestIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)
        self.global_cache = mock.AsyncMock(spec=RobotsCache)

    @mock.patch.object(ManifestChecker, "_read_manifest", return_value={})
    @mock.patch.object(ManifestChecker, "_load_root_manifest")
    @mock.patch.object(ManifestChecker, "_collect_external_data")
    async def test_robots_disabled_at_source_level(self, *mocks):
        data = _make_external_data("http://example.com", "html", enable_robots=False)
        checker = ManifestChecker("test.yaml", CheckerOptions(enable_robots_txt=False))

        mock_instance = mock.AsyncMock()
        mock_checker_cls = mock.MagicMock(return_value=mock_instance)
        mock_checker_cls.should_check.return_value = True

        checker._checkers = [mock_checker_cls]
        counter = ManifestChecker.TasksCounter()
        await checker._check_data(counter, self.session, data, self.global_cache)

        mock_checker_cls.assert_called_once_with(self.session, None)

    @mock.patch.object(ManifestChecker, "_read_manifest", return_value={})
    @mock.patch.object(ManifestChecker, "_load_root_manifest")
    @mock.patch.object(ManifestChecker, "_collect_external_data")
    async def test_robots_unspecified_falls_back_to_cli(self, *mocks):
        data = _make_external_data("http://example.com", "html", enable_robots=None)

        checker_false = ManifestChecker(
            "test.yaml", CheckerOptions(enable_robots_txt=False)
        )
        mock_instance_f = mock.AsyncMock()
        mock_checker_f = mock.MagicMock(return_value=mock_instance_f)
        mock_checker_f.should_check.return_value = True
        checker_false._checkers = [mock_checker_f]

        await checker_false._check_data(
            ManifestChecker.TasksCounter(), self.session, data, self.global_cache
        )
        mock_checker_f.assert_called_once_with(self.session, None)

        checker_true = ManifestChecker(
            "test.yaml", CheckerOptions(enable_robots_txt=True)
        )
        mock_instance_t = mock.AsyncMock()
        mock_checker_t = mock.MagicMock(return_value=mock_instance_t)
        mock_checker_t.should_check.return_value = True
        checker_true._checkers = [mock_checker_t]

        await checker_true._check_data(
            ManifestChecker.TasksCounter(), self.session, data, self.global_cache
        )
        mock_checker_t.assert_called_once_with(self.session, self.global_cache)

    @mock.patch.object(ManifestChecker, "_read_manifest", return_value={})
    @mock.patch.object(ManifestChecker, "_load_root_manifest")
    @mock.patch.object(ManifestChecker, "_collect_external_data")
    async def test_global_flag_overrides_source_disabled(self, *mocks):
        data = _make_external_data("http://example.com", "html", enable_robots=False)
        checker = ManifestChecker("test.yaml", CheckerOptions(enable_robots_txt=True))

        mock_instance = mock.AsyncMock()
        mock_checker_cls = mock.MagicMock(return_value=mock_instance)
        mock_checker_cls.should_check.return_value = True
        checker._checkers = [mock_checker_cls]

        await checker._check_data(
            ManifestChecker.TasksCounter(), self.session, data, self.global_cache
        )

        mock_checker_cls.assert_called_once_with(self.session, self.global_cache)


class TestUtilsRobotsCoverage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = mock.AsyncMock(spec=aiohttp.ClientSession)
        self.robots_cache = mock.AsyncMock(spec=RobotsCache)

    async def test_get_extra_data_info_from_url_redirect(self):
        url = "http://example.com/file.tar.gz"
        redir_url = URL("http://example.org/file.tar.gz")

        mock_resp = _make_mock_resp(
            content_bytes=b"data",
            headers={
                "Content-Type": "application/x-tar",
                "Date": "Fri, 24 Apr 2026 12:00:00 GMT",
            },
            url=redir_url,
        )

        with mock.patch.object(self.session, "get") as mock_get:
            mock_get.return_value.__aenter__.return_value = mock_resp
            await get_extra_data_info_from_url(
                url, self.session, robots_cache=self.robots_cache
            )

        self.assertEqual(self.robots_cache.ensure_allowed.call_count, 2)

    async def test_get_timestamp_from_url_redirect(self):
        url = "http://example.com/start"
        redir_url = "http://example.org/end"

        mock_resp = mock.AsyncMock()
        mock_resp.url = URL(redir_url)
        mock_resp.headers = {"Last-Modified": "Fri, 24 Apr 2026 12:00:00 GMT"}

        with (
            mock.patch.object(self.session, "head") as mock_head,
            mock.patch("src.lib.utils._extract_timestamp", return_value=123456789),
        ):
            mock_head.return_value.__aenter__.return_value = mock_resp
            await get_timestamp_from_url(
                url, self.session, robots_cache=self.robots_cache
            )

        self.assertEqual(self.robots_cache.ensure_allowed.call_count, 2)
        self.robots_cache.ensure_allowed.assert_any_call(url)
        self.robots_cache.ensure_allowed.assert_any_call(redir_url)


if __name__ == "__main__":
    unittest.main()
