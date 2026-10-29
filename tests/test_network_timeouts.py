import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock

import aiohttp
from yarl import URL

from src.checkers.jsonchecker import JSONChecker
from src.lib import HTTP_CHUNK_SIZE
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerFetchError, CheckerQueryError
from src.lib.externaldata import ExternalData
from src.manifest import ManifestChecker


class TestNetworkTimeouts(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _source(name):
        return {
            "type": "extra-data",
            "filename": f"{name}.bin",
            "url": f"https://example.com/{name}-1.bin",
            "sha256": "0" * 64,
            "size": 0,
            "x-checker-data": {
                "type": "json",
                "version-query": '"2"',
                "url-query": json.dumps(f"https://example.com/{name}-2.bin"),
            },
        }

    @staticmethod
    def _response(url, error=None):
        async def chunks():
            yield b"partial download"
            if error is not None:
                raise error

        response = mock.MagicMock()
        response.url = URL(url)
        response.headers = {}
        response.content.iter_chunked.return_value = chunks()
        context = mock.MagicMock()
        context.__aenter__.return_value = response
        return context, response

    async def test_json_download_timeout_becomes_fetch_error(self):
        # aiohttp's total timeout is plain asyncio.TimeoutError, not ClientError.
        timeout = asyncio.TimeoutError()
        session = mock.MagicMock(spec=aiohttp.ClientSession)
        context, response = self._response("https://example.com/baidu-2.bin", timeout)
        session.get.return_value = context
        data = ExternalData.from_source("test.json", self._source("baidu"))

        with self.assertRaises(CheckerFetchError) as caught:
            await JSONChecker(session).check(data)

        self.assertIs(caught.exception.__cause__, timeout)
        self.assertIsNone(data.new_version)
        response.content.iter_chunked.assert_called_once_with(HTTP_CHUNK_SIZE)
        context.__aexit__.assert_awaited_once()

    async def test_json_download_cancellation_propagates(self):
        cancellation = asyncio.CancelledError()
        session = mock.MagicMock(spec=aiohttp.ClientSession)
        context, _ = self._response("https://example.com/baidu-2.bin", cancellation)
        session.get.return_value = context
        data = ExternalData.from_source("test.json", self._source("baidu"))

        with self.assertRaises(asyncio.CancelledError) as caught:
            await JSONChecker(session).check(data)

        self.assertIs(caught.exception, cancellation)
        self.assertIsNone(data.new_version)
        context.__aexit__.assert_awaited_once()

    async def test_manifest_continues_after_download_timeout(self):
        timeout = asyncio.TimeoutError()
        responses = {}

        def get(url, **kwargs):
            context, response = self._response(
                url, timeout if "baidu" in str(url) else None
            )
            responses[str(url)] = response
            return context

        session = mock.MagicMock(spec=aiohttp.ClientSession)
        session.get.side_effect = get
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "sources.json")
            with open(path, "w") as stream:
                json.dump([self._source("baidu"), self._source("independent")], stream)
            checker = ManifestChecker(path)
            checker._checkers = [JSONChecker]
            with mock.patch("src.manifest.aiohttp.ClientSession") as session_cls:
                session_cls.return_value.__aenter__.return_value = session
                checked = await checker.check()

        self.assertEqual(len(checked), 2)
        failed, independent = checker.get_external_data()
        self.assertIsNone(failed.new_version)
        self.assertTrue(failed.checked.is_set())
        self.assertEqual(independent.new_version.version, "2")
        self.assertEqual(independent.new_version.size, len(b"partial download"))
        self.assertTrue(independent.checked.is_set())
        errors = checker.get_errors()
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], CheckerFetchError)
        self.assertIs(errors[0].__cause__, timeout)
        self.assertEqual(len(responses), 2)

    async def test_metadata_timeout_becomes_query_error(self):
        timeout = asyncio.TimeoutError()
        session = mock.MagicMock(spec=aiohttp.ClientSession)
        context, response = self._response("https://example.com/releases.json")
        response.json = mock.AsyncMock(side_effect=timeout)
        session.get.return_value = context

        with self.assertRaises(CheckerQueryError) as caught:
            await JSONChecker(session)._get_json("https://example.com/releases.json")

        self.assertIs(caught.exception.__cause__, timeout)

    async def test_digest_stream_timeout_becomes_fetch_error(self):
        timeout = asyncio.TimeoutError()
        session = mock.MagicMock(spec=aiohttp.ClientSession)
        context, response = self._response("https://example.com/file.bin")

        async def chunks():
            yield b"partial download", False
            raise timeout

        response.content.iter_chunks.return_value = chunks()
        session.get.return_value = context
        with self.assertRaises(CheckerFetchError) as caught:
            await JSONChecker(session)._complete_digests(
                "https://example.com/file.bin", MultiDigest(sha256="0" * 64)
            )

        self.assertIs(caught.exception.__cause__, timeout)
