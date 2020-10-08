#!/usr/bin/env python3
# Copyright © 2020 Maximiliano Sandoval <msandova@protonmail.com>
#
# Authors:
#       Maximiliano Sandoval <msandova@protonmail.com>
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
    os.path.dirname(__file__), "org.gnome.baobab.json"
)

class TestGNOMEChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        data = self._find_by_filename(ext_data, "baobab-3.34.0.tar.xz")

        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "baobab-3.34.0.tar.xz")
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https://download\.gnome\.org/sources/baobab/.+/baobab-.+\.tar\.xz$",  # noqa: E501
        )
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, str)
        self.assertNotEqual(
            data.new_version.checksum,
            "0000000000000000000000000000000000000000000000000000000000000000",
        )

    def test_check_library(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        data = self._find_by_filename(ext_data, "libhandy-0.81.0.tar.xz")

        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "libhandy-0.81.0.tar.xz")
        self.assertIsNotNone(data.new_version)
        self.assertEqual(
            data.new_version.version, "1.0.0",
        )
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, str)
        self.assertEqual(
            data.new_version.checksum,
            "a9398582f47b7d729205d6eac0c068fef35aaf249fdd57eea3724f8518d26699",
        )

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        else:
            return None


if __name__ == "__main__":
    unittest.main()
