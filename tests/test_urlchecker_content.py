import copy
import gzip
import hashlib
import unittest
from unittest import mock

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.checkers.urlchecker import URLChecker
from src.lib.errors import CheckerFetchError
from src.lib.externaldata import ExternalData


class TestURLCheckerContent(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.body = b"<!DOCTYPE html><html><body>Download moved</body></html>"
        self.content_type = "text/html"
        app = web.Application()
        app.router.add_get("/landing", self.respond)
        app.router.add_get("/direct", self.respond)
        app.router.add_get("/download/{filename}", self.redirect)
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.session = aiohttp.ClientSession(raise_for_status=True)
        self.addAsyncCleanup(self.session.close)
        self.checker = URLChecker(self.session)

    async def respond(self, request):
        return web.Response(body=self.body, content_type=self.content_type)

    async def redirect(self, request):
        raise web.HTTPFound("/landing")

    def make_source(self, source_type="extra-data", rotating=False, path="/direct"):
        url = str(self.server.make_url(path))
        source = {
            "type": source_type,
            "url": url,
            "sha256": "0" * 64,
            "size": 123,
            "filename": "download",
        }
        if rotating:
            source["x-checker-data"] = {"type": "rotating-url", "url": url}
        return ExternalData.from_source("test.json", source)

    async def assert_rejected(self, data):
        original_source = copy.deepcopy(data.source)
        original_version = data.current_version
        with self.assertRaisesRegex(CheckerFetchError, "Wrong content type") as caught:
            await self.checker.check(data)
        self.assertIsNone(data.new_version)
        self.assertEqual(data.current_version, original_version)
        self.assertFalse(data.state & (data.State.VALID | data.State.OUTDATED))
        data.update()
        self.assertEqual(data.source, original_source)
        return str(caught.exception)

    async def test_html_redirects(self):
        for source_type, rotating in (
            ("extra-data", False),
            ("extra-data", True),
            ("archive", True),
            ("file", True),
        ):
            for filename in ("package.tar.gz", "package.deb", "package.AppImage"):
                with self.subTest(type=source_type, rotating=rotating, file=filename):
                    data = self.make_source(
                        source_type, rotating, f"/download/{filename}"
                    )
                    with (
                        mock.patch(
                            "src.checkers.urlchecker.utils.extract_appimage_version"
                        ) as appimage,
                        mock.patch(
                            "src.checkers.urlchecker.utils.extract_deb_version"
                        ) as deb,
                    ):
                        error = await self.assert_rejected(data)
                        appimage.assert_not_called()
                        deb.assert_not_called()
                    self.assertIn(data.current_version.url, error)
                    self.assertIn(str(self.server.make_url("/landing")), error)
                    self.assertIn("redirected from", error)

    async def test_html_with_binary_header(self):
        self.content_type = "application/octet-stream"
        for rotating in (False, True):
            with self.subTest(rotating=rotating):
                await self.assert_rejected(self.make_source(rotating=rotating))

    async def test_valid_binary_with_html_header(self):
        self.body = gzip.compress(b"A valid compressed download", mtime=0)
        self.content_type = "text/html"
        for source_type, rotating in (
            ("extra-data", False),
            ("extra-data", True),
            ("archive", True),
            ("file", True),
        ):
            with self.subTest(type=source_type, rotating=rotating):
                data = self.make_source(source_type, rotating, "/download/package.gz")
                await self.checker.check(data)
                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.new_version.size, len(self.body))
                data.update()
                self.assertEqual(
                    data.source["sha256"], hashlib.sha256(self.body).hexdigest()
                )
                expected_url = (
                    str(self.server.make_url("/landing"))
                    if rotating
                    else data.current_version.url
                )
                self.assertEqual(data.source["url"], expected_url)

    async def test_plain_text_files_allowed(self):
        self.body = b"#!/bin/sh\necho 'A legitimate text download'\n"
        self.content_type = "text/plain"
        for source_type, rotating in (
            ("extra-data", False),
            ("extra-data", True),
            ("file", True),
        ):
            with self.subTest(type=source_type, rotating=rotating):
                data = self.make_source(source_type, rotating)
                await self.checker.check(data)
                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.new_version.size, len(self.body))

    async def test_plain_text_archive_rejected(self):
        self.body = b"The requested archive is no longer available.\n"
        self.content_type = "application/gzip"
        await self.assert_rejected(self.make_source("archive", rotating=True))
