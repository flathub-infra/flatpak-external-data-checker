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
import unittest

from src.lib.utils import init_logging
from src.checker import ManifestChecker

TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__), "com.adobe.Flash-Player-Projector.json"
)


class TestHTMLChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        data = self._find_by_filename(ext_data, "flash_player_sa_linux.x86_64.tar.gz")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "flash_player_sa_linux.x86_64.tar.gz")
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https?://fpdownload\.macromedia\.com/pub/flashplayer/updaters/.+/flash_player_sa_linux\.x86_64\.tar\.gz$",  # noqa: E501
        )
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, str)
        self.assertNotEqual(
            data.new_version.checksum,
            "0000000000000000000000000000000000000000000000000000000000000000",
        )

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        else:
            return None


if __name__ == "__main__":
    unittest.main()
