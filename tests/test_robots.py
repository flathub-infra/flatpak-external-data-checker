import asyncio
import json
import time
import unittest
import urllib.parse
import urllib.robotparser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import aiohttp

from src.lib.errors import CheckerFetchError
from src.lib.robots import CACHE_TTL, RobotsCache


def _fake_get(status, body=""):
    class Resp:
        def __init__(self):
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def text(self):
            return body

    def _get(*args, **kwargs):
        return Resp()

    return _get


def _allow_all():
    rp = urllib.robotparser.RobotFileParser()
    rp.parse([])
    return rp


def _disallow_all():
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(["User-agent: *", "Disallow: /"])
    return rp


class TestRobotsCache(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmpdir = TemporaryDirectory()
        self.cache_dir = Path(self._tmpdir.name)
        self.session = aiohttp.ClientSession()
        self.cache = RobotsCache(self.session, cache_dir=self.cache_dir)

    async def asyncTearDown(self):
        await self.session.close()
        self._tmpdir.cleanup()

    async def test_allow_all(self):
        with mock.patch.object(
            self.cache, "_fetch_parser", new=mock.AsyncMock(return_value=_allow_all())
        ):
            self.assertTrue(await self.cache._is_allowed("https://example.com/foo"))

    async def test_disallow_all(self):
        with mock.patch.object(
            self.cache,
            "_fetch_parser",
            new=mock.AsyncMock(return_value=_disallow_all()),
        ):
            self.assertFalse(await self.cache._is_allowed("https://example.com/foo"))

    async def test_ensure_allowed_raises(self):
        with mock.patch.object(
            self.cache,
            "_fetch_parser",
            new=mock.AsyncMock(return_value=_disallow_all()),
        ):
            with self.assertRaises(CheckerFetchError):
                await self.cache.ensure_allowed("https://example.com/foo")

    async def test_ensure_allowed_ok(self):
        with mock.patch.object(
            self.cache, "_fetch_parser", new=mock.AsyncMock(return_value=_allow_all())
        ):
            await self.cache.ensure_allowed("https://example.com/foo")

    async def test_memory_cache_used(self):
        fetch = mock.AsyncMock(return_value=_allow_all())
        with mock.patch.object(self.cache, "_fetch_parser", new=fetch):
            await self.cache._is_allowed("https://example.com/foo")
            await self.cache._is_allowed("https://example.com/bar")
            self.assertEqual(fetch.call_count, 1)

    async def test_memory_cache_populated_from_disk(self):
        self.cache._save_to_disk("example.com", [])
        fetch = mock.AsyncMock()
        with mock.patch.object(self.cache, "_fetch_parser", new=fetch):
            await self.cache._is_allowed("https://example.com/a")
            await self.cache._is_allowed("https://example.com/b")
            fetch.assert_not_called()

    async def test_disk_cache_roundtrip(self):
        self.cache._save_to_disk("example.com", ["User-agent: *", "Disallow: /"])
        rp = self.cache._load_from_disk("example.com")
        self.assertIsNotNone(rp)
        self.assertFalse(rp.can_fetch("*", "https://example.com/foo"))

    async def test_disk_cache_expiry(self):
        path = self.cache._cache_path("example.com")
        path.write_text(
            json.dumps(
                {
                    "timestamp": time.time() - (CACHE_TTL + 10),
                    "lines": ["User-agent: *", "Disallow: /"],
                }
            )
        )
        self.assertIsNone(self.cache._load_from_disk("example.com"))
        self.assertFalse(path.exists())

    async def test_disk_cache_survives_new_instance(self):
        self.cache._save_to_disk("example.com", ["User-agent: *", "Disallow: /"])
        cache2 = RobotsCache(self.session, cache_dir=self.cache_dir)
        fetch = mock.AsyncMock()
        with mock.patch.object(cache2, "_fetch_parser", new=fetch):
            allowed = await cache2._is_allowed("https://example.com/foo")
            fetch.assert_not_called()
        self.assertFalse(allowed)

    async def test_invalid_cache_file(self):
        self.cache._cache_path("example.com").write_text("not-json")
        self.assertIsNone(self.cache._load_from_disk("example.com"))

    async def test_cache_overwrite(self):
        self.cache._save_to_disk("example.com", ["User-agent: *", "Disallow: /"])
        self.cache._save_to_disk("example.com", ["User-agent: *"])
        rp = self.cache._load_from_disk("example.com")
        self.assertTrue(rp.can_fetch("*", "https://example.com/foo"))

    async def test_fetch_404_allows(self):
        with mock.patch.object(self.session, "get", side_effect=_fake_get(404)):
            self.assertTrue(await self.cache._is_allowed("https://example.com/foo"))

    async def test_fetch_403_disallows(self):
        with mock.patch.object(self.session, "get", side_effect=_fake_get(403)):
            self.assertFalse(await self.cache._is_allowed("https://example.com/foo"))

    async def test_fetch_403_ensure_allowed_raises(self):
        with mock.patch.object(self.session, "get", side_effect=_fake_get(403)):
            with self.assertRaises(CheckerFetchError):
                await self.cache.ensure_allowed("https://example.com/pkg.tar.gz")

    async def test_fetch_404_ensure_allowed_ok(self):
        with mock.patch.object(self.session, "get", side_effect=_fake_get(404)):
            await self.cache.ensure_allowed("https://example.com/pkg.tar.gz")

    async def test_fetch_error_allows(self):
        with mock.patch.object(
            self.session, "get", side_effect=aiohttp.ClientError("error")
        ):
            self.assertTrue(await self.cache._is_allowed("https://example.com/foo"))

    async def test_network_error_not_cached_to_disk(self):
        with mock.patch.object(
            self.session, "get", side_effect=aiohttp.ClientError("error")
        ):
            await self.cache._is_allowed("https://example.com/foo")
        self.assertFalse(self.cache._cache_path("example.com").exists())

    async def test_timeout_fails_open(self):
        with mock.patch.object(self.session, "get", side_effect=asyncio.TimeoutError()):
            self.assertTrue(await self.cache._is_allowed("https://example.com/foo"))

    async def test_timeout_not_cached_to_disk(self):
        with mock.patch.object(self.session, "get", side_effect=asyncio.TimeoutError()):
            await self.cache._is_allowed("https://example.com/foo")
        self.assertFalse(self.cache._cache_path("example.com").exists())

    async def test_multiple_netlocs(self):

        async def fake_fetch(robots_url, netloc):
            return _allow_all() if "a." in netloc else _disallow_all()

        with mock.patch.object(self.cache, "_fetch_parser", side_effect=fake_fetch):
            self.assertTrue(await self.cache._is_allowed("https://a.example.com/x"))
            self.assertFalse(await self.cache._is_allowed("https://b.example.com/x"))

    async def test_parse_result_accepted(self):
        with mock.patch.object(
            self.cache, "_fetch_parser", new=mock.AsyncMock(return_value=_allow_all())
        ):
            parsed = urllib.parse.urlparse("https://example.com/path")
            self.assertTrue(await self.cache._is_allowed(parsed))

    async def test_concurrent_same_netloc_fetches_once(self):
        call_count = 0

        async def counting_fetch(robots_url, netloc):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0)
            return _allow_all()

        with mock.patch.object(self.cache, "_fetch_parser", side_effect=counting_fetch):
            await asyncio.gather(
                self.cache._is_allowed("https://example.com/a"),
                self.cache._is_allowed("https://example.com/b"),
            )
        self.assertEqual(call_count, 1)

    async def test_expired_disk_triggers_refetch(self):
        path = self.cache._cache_path("example.com")
        path.write_text(
            json.dumps(
                {
                    "timestamp": time.time() - (CACHE_TTL + 1),
                    "lines": ["User-agent: *", "Disallow: /"],
                }
            )
        )
        fetch = mock.AsyncMock(return_value=_allow_all())

        with mock.patch.object(self.cache, "_fetch_parser", new=fetch):
            allowed = await self.cache._is_allowed("https://example.com/foo")
            fetch.assert_called_once()
        self.assertTrue(allowed)

    async def test_load_from_disk_invalid_data(self):
        bad_json = '{"timestamp": 123, "lines": "bad_list"}'
        bad_dict = "[1, 2, 3]"

        with mock.patch("src.lib.robots.Path.read_text", return_value=bad_json):
            self.assertIsNone(self.cache._load_from_disk("example.com"))

        with mock.patch("src.lib.robots.Path.read_text", return_value=bad_dict):
            self.assertIsNone(self.cache._load_from_disk("example.com"))

    async def test_disk_errors_handling(self):
        with (
            mock.patch(
                "src.lib.robots.Path.read_text", side_effect=OSError("disk error")
            ),
            mock.patch("src.lib.robots.log") as mock_log,
        ):
            self.assertIsNone(self.cache._load_from_disk("example.com"))
            mock_log.debug.assert_called()

        with (
            mock.patch(
                "src.lib.robots.Path.write_text", side_effect=OSError("disk error")
            ),
            mock.patch("src.lib.robots.log") as mock_log,
        ):
            self.cache._save_to_disk("example.com", ["User-agent: *"])
            mock_log.debug.assert_called()

    async def test_symlink_checks(self):
        with (
            mock.patch("src.lib.robots.Path.is_symlink", return_value=True),
            self.assertRaisesRegex(RuntimeError, "must not be a symlink"),
        ):
            RobotsCache(self.session, cache_dir=Path("/tmp/fake-robots"))

        with (
            mock.patch("src.lib.robots.Path.resolve", return_value=Path("/etc/passwd")),
            self.assertRaisesRegex(RuntimeError, "outside cache directory"),
        ):
            self.cache._cache_path("example.com")


if __name__ == "__main__":
    unittest.main()
