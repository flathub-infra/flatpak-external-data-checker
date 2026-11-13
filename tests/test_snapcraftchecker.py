import asyncio
import datetime
import hashlib
import os
import unittest
from contextlib import asynccontextmanager
from unittest import mock

import aiohttp
from yarl import URL

from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerFetchError, CheckerQueryError
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
        await self._check_download(checksum_matches=True)

    async def test_checksum_mismatch_does_not_offer_update(self):
        await self._check_download(checksum_matches=False)

    async def _check_download(self, checksum_matches):
        api_url = "https://api.snapcraft.io/v2/snaps/info/nordpass"
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
        if not checksum_matches:
            release["download"]["sha3-384"] = "0" * 96

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
        original = self._find_by_filename(checker.get_external_data(), "nordpass.snap")
        with (
            mock.patch("aiohttp.ClientSession.get", side_effect=response) as get,
            mock.patch(
                "src.checkers.urlchecker.utils.get_extra_data_info_from_url",
                new_callable=mock.AsyncMock,
                return_value=original.current_version,
            ) as validate_current,
        ):
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
        if not checksum_matches:
            validate_current.assert_awaited_once()
            self.assertEqual(
                validate_current.call_args.args[0], original.current_version.url
            )
            self.assertIsNone(data.new_version)
            self.assertFalse(data.state & data.State.LATEST)
            return
        validate_current.assert_not_awaited()
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
        await self._check_unchanged_release(False)

    async def test_unchanged_release_download_timeout_has_detail(self):
        checker = ManifestChecker(TEST_MANIFEST)
        data = self._find_by_filename(checker.get_external_data(), "nordpass.snap")
        current = data.current_version
        timeout = asyncio.TimeoutError()
        metadata = {
            "channel-map": [
                {
                    "channel": {"architecture": "amd64", "name": "stable"},
                    "download": {"url": current.url},
                    "version": "2.0.0",
                }
            ]
        }

        async def chunks(block_size):
            yield b"partial download"
            raise timeout

        @asynccontextmanager
        async def response(url, **kwargs):
            if url == "https://api.snapcraft.io/v2/snaps/info/nordpass":
                self.assertEqual(kwargs, {"headers": {"Snap-Device-Series": "16"}})
                yield mock.Mock(json=mock.AsyncMock(return_value=metadata))
            elif url == current.url:
                yield mock.Mock(
                    url=URL(url), headers={}, content=mock.Mock(iter_chunked=chunks)
                )
            else:
                self.fail(f"Unexpected HTTP request: {url}")

        with (
            mock.patch("aiohttp.ClientSession.get", side_effect=response) as get,
            self.assertLogs("src.manifest", level="ERROR") as logs,
        ):
            await checker.check()
        self.assertEqual(get.call_count, 2)
        (error,) = checker.get_errors()
        self.assertIsInstance(error, CheckerFetchError)
        self.assertIs(error.__cause__, timeout)
        message = f"Error downloading upstream source: {type(timeout).__name__}"
        self.assertEqual(str(error), message)
        self.assertIn(f"with URLChecker: {message}", logs.output[0])
        self.assertTrue(all(r.exc_info is None for r in logs.records))
        self.assertTrue(data.check_failed)
        self.assertTrue(data.checked.is_set())
        self.assertIsNone(data.new_version)
        self.assertEqual(data.current_version, current)
        self.assertFalse(data.state & data.State.LATEST)
        report = checker.get_source_report()[0]
        self.assertEqual(report["checkers_run"], ["SnapcraftChecker", "URLChecker"])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(
            report["errors"], [{"type": "CheckerFetchError", "message": message}]
        )

    async def test_api_failures_are_reported_per_source(self):
        await self._check_request_failures(download=False)

    async def test_download_failures_are_reported_per_source(self):
        await self._check_request_failures(download=True)

    async def _check_request_failures(self, download):
        api_url = "https://api.snapcraft.io/v2/snaps/info/nordpass"
        download_url = "https://example.invalid/nordpass.snap"
        failed_url = download_url if download else api_url
        failures = [
            (
                "http",
                aiohttp.ClientResponseError(
                    mock.Mock(real_url=URL(failed_url)),
                    (),
                    status=429,
                    message="Too Many Requests",
                ),
            ),
            ("connection", aiohttp.ClientConnectionError("Connection lost")),
            ("timeout", asyncio.TimeoutError("Request timed out")),
            ("body", aiohttp.ClientPayloadError("Incomplete response")),
        ]
        metadata = {
            "channel-map": [
                {
                    "channel": {"architecture": "amd64", "name": "stable"},
                    "download": {"url": download_url, "sha3-384": "unused"},
                    "version": "2.0.0",
                }
            ]
        }
        for stage, failure in failures:
            with self.subTest(stage=stage):
                checker = ManifestChecker(TEST_MANIFEST)
                data = self._find_by_filename(
                    checker.get_external_data(), "nordpass.snap"
                )
                current = data.current_version

                async def chunks(block_size, failure=failure):
                    yield b"partial download"
                    raise failure

                @asynccontextmanager
                async def response(url, stage=stage, failure=failure, **kwargs):
                    if url == api_url:
                        self.assertEqual(
                            kwargs, {"headers": {"Snap-Device-Series": "16"}}
                        )
                    if url == failed_url:
                        if stage != "body":
                            raise failure
                        yield mock.Mock(
                            json=mock.AsyncMock(side_effect=failure),
                            content=mock.Mock(iter_chunked=chunks),
                        )
                    elif download and url == api_url:
                        yield mock.Mock(json=mock.AsyncMock(return_value=metadata))
                    else:
                        self.fail(f"Unexpected HTTP request: {url}")

                with (
                    mock.patch(
                        "aiohttp.ClientSession.get", side_effect=response
                    ) as get,
                    self.assertLogs("src.manifest", level="ERROR") as logs,
                ):
                    await checker.check()
                self.assertEqual(get.call_count, 2 if download else 1)
                errors = checker.get_errors()
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(
                    errors[0], CheckerFetchError if download else CheckerQueryError
                )
                self.assertIs(errors[0].__cause__, failure)
                self.assertTrue(all(r.exc_info is None for r in logs.records))
                self.assertTrue(data.check_failed)
                self.assertTrue(data.checked.is_set())
                self.assertIsNone(data.new_version)
                self.assertEqual(data.current_version, current)
                self.assertFalse(data.state & data.State.LATEST)
                report = next(
                    s
                    for s in checker.get_source_report()
                    if s["checker_type"] == "snapcraft"
                )
                self.assertEqual(report["status"], "failed")

    async def test_unchanged_release_repairs_download_metadata(self):
        await self._check_unchanged_release(True)

    async def _check_unchanged_release(self, changed):
        checker = ManifestChecker(TEST_MANIFEST)
        data = self._find_by_filename(checker.get_external_data(), "nordpass.snap")
        downloaded = data.current_version
        if changed:
            downloaded = downloaded._replace(
                checksum=MultiDigest(sha256="a" * 64),
                size=123,
            )
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
                "src.checkers.urlchecker.utils.get_extra_data_info_from_url",
                new_callable=mock.AsyncMock,
                return_value=downloaded,
            ) as download,
        ):
            await checker.check()
        get.assert_called_once_with(
            "https://api.snapcraft.io/v2/snaps/info/nordpass",
            headers={"Snap-Device-Series": "16"},
        )
        download.assert_awaited_once()
        report = next(
            s for s in checker.get_source_report() if s["checker_type"] == "snapcraft"
        )
        self.assertEqual(report["status"], "update_available" if changed else "checked")
        self.assertEqual(report["selected_version"], "2.0.0")
        if changed:
            self.assertEqual(data.new_version.checksum, downloaded.checksum)
            self.assertEqual(data.new_version.size, downloaded.size)
        else:
            self.assertIsNone(data.new_version)

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        return None


if __name__ == "__main__":
    unittest.main()
