import asyncio
import importlib
import json
import os
import time
import unittest
import urllib.parse
import urllib.robotparser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import aiohttp

from src.lib import robots as robots_mod
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

    async def test_http_https_memory_isolation(self):
        fetch = mock.AsyncMock(side_effect=[_allow_all(), _disallow_all()])
        with mock.patch.object(self.cache, "_fetch_parser", new=fetch):
            for _ in range(2):
                self.assertTrue(await self.cache._is_allowed("http://example.com/a"))
                self.assertFalse(await self.cache._is_allowed("https://example.com/a"))
        self.assertEqual(fetch.await_count, 2)

    async def test_origin_disk_isolation(self):
        urls = [
            "http://example.com/a",
            "https://example.com/a",
            "https://example.com:8443/a",
        ]
        responses = [_fake_get(404)(), _fake_get(403)(), _fake_get(404)()]
        # Each request starts with an empty memory cache but shares persisted rules.
        with mock.patch.object(self.session, "get", side_effect=responses) as get:
            for url, allowed in zip(urls, [True, False, True], strict=True):
                cache = RobotsCache(self.session, cache_dir=self.cache_dir)
                self.assertEqual(await cache._is_allowed(url), allowed)
        self.assertEqual(get.call_count, 3)

        cache = RobotsCache(self.session, cache_dir=self.cache_dir)
        with mock.patch.object(self.session, "get") as get:
            for url, allowed in zip(urls, [True, False, True], strict=True):
                self.assertEqual(await cache._is_allowed(url), allowed)
            get.assert_not_called()

    async def test_legacy_netloc_cache_ignored(self):
        self.cache._save_to_disk("example.com", ["User-agent: *", "Disallow: /"])
        with mock.patch.object(self.session, "get", side_effect=_fake_get(404)) as get:
            self.assertTrue(await self.cache._is_allowed("http://example.com/a"))
            self.assertTrue(await self.cache._is_allowed("https://example.com/a"))
        self.assertEqual(get.call_count, 2)

    async def test_equivalent_origins_share_cache(self):
        for scheme, port in [("http", 80), ("https", 443)]:
            with self.subTest(scheme=scheme):
                with mock.patch.object(
                    self.session, "get", side_effect=_fake_get(403)
                ) as get:
                    self.assertFalse(
                        await self.cache._is_allowed(
                            f"{scheme}://user:password@EXAMPLE.com:{port}/a?q=1#part"
                        )
                    )
                    self.assertFalse(
                        await self.cache._is_allowed(f"{scheme}://example.com/b")
                    )
                    get.assert_called_once_with(
                        f"{scheme}://example.com:{port}/robots.txt",
                        raise_for_status=False,
                    )
                cache = RobotsCache(self.session, cache_dir=self.cache_dir)
                with mock.patch.object(self.session, "get") as get:
                    self.assertFalse(
                        await cache._is_allowed(f"{scheme}://example.com/c")
                    )
                    get.assert_not_called()

    async def test_ipv6_robots_url(self):
        with mock.patch.object(self.session, "get", side_effect=_fake_get(404)) as get:
            self.assertTrue(await self.cache._is_allowed("https://[::1]:8443/a?q=1"))
            get.assert_called_once_with(
                "https://[::1]:8443/robots.txt", raise_for_status=False
            )

    async def test_memory_cache_populated_from_disk(self):
        self.cache._save_to_disk("https://example.com:443", [])
        fetch = mock.AsyncMock()
        with mock.patch.object(self.cache, "_fetch_parser", new=fetch):
            await self.cache._is_allowed("https://example.com/a")
            await self.cache._is_allowed("https://example.com/b")
            fetch.assert_not_called()

    async def test_disk_cache_roundtrip(self):
        self.cache._save_to_disk(
            "https://example.com:443", ["User-agent: *", "Disallow: /"]
        )
        rp = self.cache._load_from_disk("https://example.com:443")
        self.assertIsNotNone(rp)
        self.assertFalse(rp.can_fetch("*", "https://example.com/foo"))

    async def test_disk_cache_expiry(self):
        path = self.cache._cache_path("https://example.com:443")
        path.write_text(
            json.dumps(
                {
                    "timestamp": time.time() - (CACHE_TTL + 10),
                    "lines": ["User-agent: *", "Disallow: /"],
                }
            )
        )
        self.assertIsNone(self.cache._load_from_disk("https://example.com:443"))
        self.assertFalse(path.exists())

    async def test_disk_cache_survives_new_instance(self):
        self.cache._save_to_disk(
            "https://example.com:443", ["User-agent: *", "Disallow: /"]
        )
        cache2 = RobotsCache(self.session, cache_dir=self.cache_dir)
        fetch = mock.AsyncMock()
        with mock.patch.object(cache2, "_fetch_parser", new=fetch):
            allowed = await cache2._is_allowed("https://example.com/foo")
            fetch.assert_not_called()
        self.assertFalse(allowed)

    async def test_invalid_cache_file(self):
        self.cache._cache_path("https://example.com:443").write_text("not-json")
        self.assertIsNone(self.cache._load_from_disk("https://example.com:443"))

    async def test_cache_overwrite(self):
        self.cache._save_to_disk(
            "https://example.com:443", ["User-agent: *", "Disallow: /"]
        )
        self.cache._save_to_disk("https://example.com:443", ["User-agent: *"])
        rp = self.cache._load_from_disk("https://example.com:443")
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
        self.assertFalse(self.cache._cache_path("https://example.com:443").exists())

    async def test_timeout_fails_open(self):
        with mock.patch.object(self.session, "get", side_effect=asyncio.TimeoutError()):
            self.assertTrue(await self.cache._is_allowed("https://example.com/foo"))

    async def test_timeout_not_cached_to_disk(self):
        with mock.patch.object(self.session, "get", side_effect=asyncio.TimeoutError()):
            await self.cache._is_allowed("https://example.com/foo")
        self.assertFalse(self.cache._cache_path("https://example.com:443").exists())

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

    async def test_concurrent_same_origin_fetches_once(self):
        call_count = 0

        async def counting_fetch(robots_url, origin):
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0)
            return _allow_all()

        with mock.patch.object(self.cache, "_fetch_parser", side_effect=counting_fetch):
            allowed = await asyncio.gather(
                self.cache._is_allowed("https://example.com/a"),
                self.cache._is_allowed("https://EXAMPLE.com:443/b"),
            )
        self.assertEqual(allowed, [True, True])
        self.assertEqual(call_count, 1)

    async def test_expired_disk_triggers_refetch(self):
        path = self.cache._cache_path("https://example.com:443")
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

    async def test_load_from_disk_lines_not_a_list(self):
        path = self.cache._cache_path("https://example.com:443")
        path.write_text(json.dumps({"timestamp": time.time(), "lines": "not-a-list"}))
        self.assertIsNone(self.cache._load_from_disk("https://example.com:443"))

    async def test_load_from_disk_data_not_a_dict(self):
        path = self.cache._cache_path("https://example.com:443")
        path.write_text(json.dumps([1, 2, 3]))
        self.assertIsNone(self.cache._load_from_disk("https://example.com:443"))

    async def test_disk_errors_handling(self):
        with (
            mock.patch(
                "src.lib.robots.Path.read_text", side_effect=OSError("disk error")
            ),
            mock.patch("src.lib.robots.log") as mock_log,
        ):
            self.assertIsNone(self.cache._load_from_disk("https://example.com:443"))
            mock_log.debug.assert_called()

        with (
            mock.patch(
                "src.lib.robots.Path.write_text", side_effect=OSError("disk error")
            ),
            mock.patch("src.lib.robots.log") as mock_log,
        ):
            self.cache._save_to_disk("https://example.com:443", ["User-agent: *"])
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
            self.cache._cache_path("https://example.com:443")

    async def test_xdg_cache_home_used(self):
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": "/tmp/xdg-test"}):
            importlib.reload(robots_mod)
            self.assertEqual(
                robots_mod.CACHE_DIR,
                Path("/tmp/xdg-test") / "fedc" / "robots",
            )

    async def test_fetch_200_parses_body(self):
        body = "User-agent: *\nDisallow: /secret"
        with mock.patch.object(self.session, "get", side_effect=_fake_get(200, body)):
            allowed_root = await self.cache._is_allowed("https://example.com/")
            allowed_secret = await self.cache._is_allowed("https://example.com/secret")
        self.assertTrue(allowed_root)
        self.assertFalse(allowed_secret)


if __name__ == "__main__":
    unittest.main()
