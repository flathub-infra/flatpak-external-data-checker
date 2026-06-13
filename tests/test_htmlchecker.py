#!/usr/bin/env python3
# Copyright © 2019 Bastien Nocera <hadess@hadess.net>
# Copyright © 2019 Endless Mobile, Inc.
#
# Authors:
#       Bastien Nocera <hadess@hadess.net>
#       Will Thompson <wjt@endlessm.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import base64
import os
import unittest
from unittest import mock

import aiohttp
import semver

from src.checkers.htmlchecker import HTMLChecker, _semantic_version
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerError, CheckerFetchError, CheckerQueryError
from src.lib.externaldata import ExternalData
from src.lib.utils import init_logging
from src.lib.version import LooseVersion
from src.manifest import ManifestChecker


class TestHTMLTools(unittest.IsolatedAsyncioTestCase):
    SAMPLES = {
        "utf-8": "🙋, 🌍!\n…"
        # TODO we want to test other encodings, but httbin(go)/base64/ supports only
        # utf-8
    }

    async def asyncSetUp(self):
        self.session = aiohttp.ClientSession(
            raise_for_status=True, timeout=aiohttp.ClientTimeout(total=5)
        )
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()

    async def asyncTearDown(self):
        await self.session.close()
        self.robots_patcher.stop()

    def _encoded_url(self, data: bytes):
        return "https://httpbingo.org/base64/decode/" + base64.b64encode(data).decode()

    def test_semantic_version_parses(self):
        self.assertEqual(_semantic_version("1.2.3"), semver.VersionInfo(1, 2, 3))

    def test_semantic_version_invalid_raises(self):
        with self.assertRaises(CheckerQueryError):
            _semantic_version("not-a-version")

    async def test_get_text_raises_on_network_error(self):
        checker = HTMLChecker(self.session)
        with mock.patch.object(
            self.session,
            "get",
            side_effect=aiohttp.ClientConnectionError("connection refused"),
        ):
            with self.assertRaises(CheckerQueryError):
                await checker._get_text("https://example.com/page.html")

    async def test_get_encoding_explicit_charset(self):
        checker = HTMLChecker(self.session)
        resp = mock.MagicMock(spec=aiohttp.ClientResponse)
        resp.headers = {aiohttp.hdrs.CONTENT_TYPE: "text/html; charset=iso-8859-1"}
        resp.url = "https://example.com/test"
        self.assertEqual(await checker._get_encoding(resp), "iso-8859-1")

    async def test_get_encoding_no_charset_falls_back_to_utf8(self):
        checker = HTMLChecker(self.session)
        resp = mock.MagicMock(spec=aiohttp.ClientResponse)
        resp.headers = {aiohttp.hdrs.CONTENT_TYPE: "text/html"}
        resp.url = "https://example.com/test"
        self.assertEqual(await checker._get_encoding(resp), "utf-8")

    async def test_get_encoding_unknown_charset_raises(self):
        checker = HTMLChecker(self.session)
        resp = mock.MagicMock(spec=aiohttp.ClientResponse)
        resp.headers = {aiohttp.hdrs.CONTENT_TYPE: "text/html; charset=not-a-charset"}
        resp.url = "https://example.com/test"
        with self.assertRaises(CheckerFetchError):
            await checker._get_encoding(resp)

    async def test_get_text(self):
        checker = HTMLChecker(self.session)

        for charset, sample in self.SAMPLES.items():
            self.assertEqual(
                await checker._get_text(self._encoded_url(sample.encode(charset))),
                sample,
            )

        with self.assertRaises(CheckerError):
            await checker._get_text("https://httpbingo.org/image/jpeg")

    async def test_get_text_unicode_decode_error_raises(self):
        checker = HTMLChecker(self.session)
        with self.assertRaises(CheckerError):
            await checker._get_text("https://httpbingo.org/image/jpeg")

    async def test_get_text_returns_decoded_body(self):
        checker = HTMLChecker(self.session)
        result = await checker._get_text(self._encoded_url(b"hello world"))
        self.assertEqual(result, "hello world")


TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.x.xeyes.yml")


class TestHTMLChecker(unittest.IsolatedAsyncioTestCase):
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
        self._test_check_with_url_template(
            self._find_by_filename(ext_data, "ico-1.0.4.tar.bz2")
        )
        self._test_combo_pattern(
            self._find_by_filename(ext_data, "libXScrnSaver-1.2.2.tar.bz2")
        )
        self._test_combo_pattern_nosort(
            self._find_by_filename(ext_data, "qrupdate-1.1.0.tar.gz")
        )
        self._test_version_filter(self._find_by_filename(ext_data, "libX11.tar.gz"))
        self._test_semver_filter(self._find_by_filename(ext_data, "semver.txt"))
        self._test_no_match(self._find_by_filename(ext_data, "libFS-1.0.7.tar.bz2"))
        self._test_invalid_url(self._find_by_filename(ext_data, "libdoesntexist.tar"))
        self._test_parent_child(
            self._find_by_filename(ext_data, "parent.txt"),
            self._find_by_filename(ext_data, "child.txt"),
        )
        self._test_get_latest_match(
            self._find_by_filename(ext_data, "libXScrnSaver-1.2.2.tar.bz2")
        )

    def _test_check_with_url_template(self, data):
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "ico-1.0.4.tar.bz2")
        self.assertIsNotNone(data.new_version)
        self.assertEqual(
            data.new_version.url,
            "https://www.x.org/releases/individual/app/ico-1.0.5.tar.bz2",
        )
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, MultiDigest)
        self.assertEqual(
            data.new_version.checksum,
            MultiDigest(
                sha256="d73b62f29eb98d850f16b76d759395180b860b613fbe1686b18eee99a6e3773f"
            ),
        )

    def _test_combo_pattern(self, data):
        self.assertIsNotNone(data)
        self.assertRegex(data.filename, r"libXScrnSaver-[\d\.-]+.tar.bz2")
        self.assertIsNotNone(data.new_version)
        self.assertLessEqual(
            LooseVersion("1.2.2"), LooseVersion(data.new_version.version)
        )
        self.assertRegex(
            data.new_version.url,
            r"^https?://www.x.org/releases/individual/lib/libXScrnSaver-[\d\.-]+.tar.bz2$",
        )
        self.assertNotEqual(
            data.new_version.checksum,
            MultiDigest(
                sha256="0000000000000000000000000000000000000000000000000000000000000000"
            ),
        )

    def _test_combo_pattern_nosort(self, data):
        self.assertIsNotNone(data)
        self.assertRegex(data.filename, r"qrupdate-[\d\.-]+.tar.gz")
        self.assertIsNotNone(data.new_version)
        self.assertLessEqual(
            LooseVersion("1.1.0"), LooseVersion(data.new_version.version)
        )
        self.assertRegex(
            data.new_version.url,
            r"^https://sourceforge\.net/projects/qrupdate/.+/qrupdate-\d[\d\.]+\d\.tar\.gz$",
        )
        self.assertNotEqual(
            data.new_version.checksum,
            MultiDigest(
                sha256="0000000000000000000000000000000000000000000000000000000000000000"
            ),
        )

    def _test_version_filter(self, data):
        self.assertIsNotNone(data)
        self.assertIsNotNone(data.new_version)
        self.assertEqual(data.new_version.version, "1.7.5")

    def _test_semver_filter(self, data):
        self.assertIsNotNone(data)
        self.assertIsNotNone(data.new_version)
        self.assertIsNotNone(data.new_version.version)
        self.assertEqual(data.new_version.version, "1.0.0+patch1")

    def _test_no_match(self, data):
        self.assertIsNotNone(data)
        self.assertIsNone(data.new_version)

    def _test_invalid_url(self, data):
        self.assertIsNotNone(data)
        self.assertIsNone(data.new_version)

    def _test_get_latest_match(self, data):
        self.assertIsNotNone(data)
        self.assertIsNotNone(data.new_version)
        self.assertGreaterEqual(
            LooseVersion(data.new_version.version), LooseVersion("1.2.2")
        )
        self.assertRegex(
            data.new_version.url,
            r"^https?://.*libXScrnSaver-[\d\.-]+\.tar\.bz2$",
        )

    def _test_parent_child(self, parent, child):
        self.assertIs(child.parent, parent)
        self.assertIsNotNone(parent.new_version)
        self.assertIsNotNone(child.new_version)
        self.assertEqual(
            child.new_version.checksum,
            # curl https://httpbingo.org/response-headers?version=1.0.0 | sha256sum
            MultiDigest(
                sha256="81f3779437618c7f9ff38b53ce6f5ed99e626ba82a7c31107400a2ef97592882"
            ),
        )
        self.assertEqual(parent.new_version.checksum, child.new_version.checksum)

    async def test_check_index_error_in_get_latest_raises(self):
        class _UnsubscriptableList(list):
            def __getitem__(self, idx):
                raise IndexError("forced")

        external_data = mock.MagicMock(spec=ExternalData)
        external_data.parent = None
        external_data.checker_data = {
            "url": "https://example.com/page.html",
            "pattern": r"(pkg-([\d.]+)\.tar\.gz)",
        }

        mock_session = mock.MagicMock(spec=aiohttp.ClientSession)
        checker = HTMLChecker(mock_session)

        with (
            mock.patch.object(checker, "should_check", return_value=True),
            mock.patch.object(
                checker,
                "_get_text",
                new=mock.AsyncMock(return_value="pkg-1.0.0.tar.gz"),
            ),
            mock.patch(
                "src.checkers.htmlchecker.filter_versioned_items",
                return_value=_UnsubscriptableList([mock.MagicMock()]),
            ),
        ):
            with self.assertRaises(CheckerQueryError):
                await checker.check(external_data)

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        return None


if __name__ == "__main__":
    unittest.main()
