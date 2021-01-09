#!/usr/bin/env python3
# Copyright © 2019 Endless Mobile, Inc.
#
# Authors:
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
    os.path.dirname(__file__),
    "com.adobe.FlashPlayer.NPAPI.json",
)


class TestFlashChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    @unittest.skip("Flash Player is EOL")
    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        self.assertEqual(len(ext_data), 2)

        for data in ext_data:
            self.assertIsNotNone(data.new_version)
            self.assertRegex(data.new_version.url, r"^https?://.*\.tar.gz$")
            self.assertIsInstance(data.new_version.size, int)
            self.assertGreater(data.new_version.size, 1024 * 1024)
            self.assertIsNotNone(data.new_version.version)


if __name__ == "__main__":
    unittest.main()
