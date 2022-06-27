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

import os
import base64
import unittest
from distutils.version import LooseVersion

import aiohttp

from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from src.lib.checksums import MultiDigest
from src.checkers import HTMLChecker
from src.lib.errors import CheckerError


class TestHTMLTools(unittest.IsolatedAsyncioTestCase):
    SAMPLES = {
        "utf-8": "🙋, 🌍!\n…"
        # TODO we want to test other encodings, but httbin(go)/base64/ supports only utf-8
    }

    async def asyncSetUp(self):
        self.session = aiohttp.ClientSession(
            raise_for_status=True, timeout=aiohttp.ClientTimeout(total=5)
        )

    async def asyncTearDown(self):
        await self.session.close()

    def _encoded_url(self, data: bytes):
        return (
            "https://httpbin.org/base64/"
            + base64.b64encode(data, altchars=b"-_").decode()
        )

    async def test_get_text(self):
        checker = HTMLChecker(self.session)

        for charset, sample in self.SAMPLES.items():
            self.assertEqual(
                await checker._get_text(self._encoded_url(sample.encode(charset))),
                sample,
            )

        with self.assertRaises(CheckerError):
            await checker._get_text("https://httpbin.org/image/jpeg")


TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.x.xeyes.yml")


class TestHTMLChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

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
        self._test_no_match(self._find_by_filename(ext_data, "libFS-1.0.7.tar.bz2"))
        self._test_invalid_url(self._find_by_filename(ext_data, "libdoesntexist.tar"))

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
            r"^https?://www.x.org/releases/individual/lib/libXScrnSaver-[\d\.-]+.tar.bz2$",  # noqa: E501
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
            r"^https://sourceforge\.net/projects/qrupdate/.+/qrupdate-\d[\d\.]+\d\.tar\.gz$",  # noqa: E501
        )
        self.assertNotEqual(
            data.new_version.checksum,
            MultiDigest(
                sha256="0000000000000000000000000000000000000000000000000000000000000000"
            ),
        )

    def _test_no_match(self, data):
        self.assertIsNotNone(data)
        self.assertIsNone(data.new_version)

    def _test_invalid_url(self, data):
        self.assertIsNotNone(data)
        self.assertIsNone(data.new_version)

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        else:
            return None


if __name__ == "__main__":
    unittest.main()
