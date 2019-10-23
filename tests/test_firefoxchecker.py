#!/usr/bin/env python3
# Copyright © 2019 Endless Mobile, Inc.
#
# Authors:
#       Andre Moreira Magalhaes <andre@endlessm.com>
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

import logging
import os
import unittest

from src.lib.externaldata import ExternalData
from src.checker import ManifestChecker

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.mozilla.Firefox.json")


class TestFirefoxChecker(unittest.TestCase):
    def setUp(self):
        logging.basicConfig(level=logging.DEBUG)

    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        def data_with_state(state):
            return [data for data in ext_data if data.state == state]

        removed = data_with_state(ExternalData.State.REMOVED)
        added = data_with_state(ExternalData.State.ADDED)
        updated = data_with_state(ExternalData.State.VALID)

        self.assertEqual(len(removed), 2)
        self.assertGreater(len(added), 0)
        self.assertEqual(len(updated), 1)

        data = updated[0]
        self.assertEqual(data.filename, "firefox.tar.bz2")
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https?://.*/linux-x86_64/en-US/firefox-.+\.tar\.bz2$",
        )
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)

        for data in removed:
            self.assertIsNone(data.new_version)
            self.assertIn(data.filename, ["foo.xpi", "bar.xpi"])

        for data in added:
            self.assertIsNotNone(data.current_version)
            self.assertRegex(
                data.current_version.url,
                r"^https?://.*/linux-x86_64/.*/.*\.xpi$",
            )
            self.assertIsInstance(data.current_version.size, int)
            self.assertGreater(data.current_version.size, 0)
            self.assertIsNotNone(data.current_version.checksum)


if __name__ == '__main__':
    unittest.main()
