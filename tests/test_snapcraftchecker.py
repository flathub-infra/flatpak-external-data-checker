import datetime
import hashlib
import os
import unittest
from contextlib import asynccontextmanager
from unittest import mock

from src.lib.checksums import MultiDigest
from src.lib.utils import init_logging
from src.manifest import ManifestChecker

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.nordpass.NordPass.yaml")


class TestSnapctaftChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    async def test_check(self):
        api_url = "http://api.snapcraft.io/v2/snaps/info/nordpass"
        download_url = (
            "https://api.snapcraft.io/api/v1/snaps/download/"
            "00CQ2MvSr0Ex7zwdGhCYTa0ZLMw3H6hf_24.snap"
        )
        # Large enough to exercise streaming over multiple chunks.
        contents = b"snap test data\n" * 10000
        release = {
            "channel": {
                "architecture": "amd64",
                "name": "stable",
                "released-at": "2026-01-01T12:00:00.000000+00:00",
            },
            "download": {
                "url": download_url,
                "size": len(contents),
                "sha3-384": hashlib.sha3_384(contents).hexdigest(),
            },
            "version": "2.0.0",
        }
        metadata = {
            "channel-map": [
                {
                    **release,
                    "channel": {**release["channel"], "architecture": "arm64"},
                    "download": {
                        **release["download"],
                        "url": "https://example.invalid/arm64.snap",
                    },
                },
                {
                    **release,
                    "channel": {**release["channel"], "name": "beta"},
                    "download": {
                        **release["download"],
                        "url": "https://example.invalid/beta.snap",
                    },
                },
                release,
            ],
        }

        async def chunks(block_size):
            for offset in range(0, len(contents), block_size):
                yield contents[offset : offset + block_size]

        @asynccontextmanager
        async def response(url, **kwargs):
            if url == api_url:
                self.assertEqual(kwargs, {"headers": {"Snap-Device-Series": "16"}})
                yield mock.Mock(json=mock.AsyncMock(return_value=metadata))
            elif url == download_url:
                yield mock.Mock(content=mock.Mock(iter_chunked=chunks))
            else:
                self.fail(f"Unexpected HTTP request: {url}")

        checker = ManifestChecker(TEST_MANIFEST)
        with mock.patch("aiohttp.ClientSession.get", side_effect=response) as get:
            ext_data = await checker.check()
        self.assertEqual(
            get.call_args_list,
            [
                mock.call(api_url, headers={"Snap-Device-Series": "16"}),
                mock.call(download_url),
            ],
        )
        self.assertEqual(checker.get_errors(), [])

        data = self._find_by_filename(ext_data, "nordpass.snap")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "nordpass.snap")
        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.url, download_url)
        self.assertEqual(data.new_version.size, len(contents))
        self.assertEqual(data.new_version.version, "2.0.0")
        self.assertEqual(
            data.new_version.timestamp,
            datetime.datetime(2026, 1, 1, 12, tzinfo=datetime.timezone.utc),
        )
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, MultiDigest)
        self.assertEqual(
            data.new_version.checksum,
            MultiDigest(
                sha256=hashlib.sha256(contents).hexdigest(),
            ),
        )

    async def test_unchanged_release_is_checked(self):
        checker = ManifestChecker(TEST_MANIFEST)
        data = self._find_by_filename(checker.get_external_data(), "nordpass.snap")
        metadata = {
            "channel-map": [
                {
                    "channel": {"architecture": "amd64", "name": "stable"},
                    "download": {"url": data.current_version.url},
                    "version": "2.0.0",
                }
            ]
        }

        @asynccontextmanager
        async def response(url, **kwargs):
            yield mock.Mock(json=mock.AsyncMock(return_value=metadata))

        with (
            mock.patch("aiohttp.ClientSession.get", side_effect=response) as get,
            mock.patch(
                "src.checkers.urlchecker.URLChecker.check", new_callable=mock.AsyncMock
            ) as fallback,
        ):
            await checker.check()
        get.assert_called_once()
        fallback.assert_not_awaited()
        report = next(
            s for s in checker.get_source_report() if s["checker_type"] == "snapcraft"
        )
        self.assertEqual(report["status"], "checked")
        self.assertEqual(report["selected_version"], "2.0.0")
        self.assertIsNone(data.new_version)

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        return None


if __name__ == "__main__":
    unittest.main()
