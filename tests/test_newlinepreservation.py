#!/usr/bin/env python3
# Copyright (C) 2021 Carles Pastor Badosa
#
# Authors:
#       Carles Pastor Badosa <cpbadosa@gmail.com>
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

import unittest
import os
import tempfile

from src.lib.utils import (
    _check_newline,
    dump_manifest,
    read_manifest,
)

MANIFEST_WITH_NEWLINE = """{
    "ends in newline": true
}
"""

MANIFEST_NO_NEWLINE = """{
    "ends in newline": false
}"""


class TestNewlinePreservation(unittest.TestCase):
    def test_newline(self):
        with tempfile.TemporaryDirectory() as d:
            fp = os.path.join(d, "trailingnewline.json")
            with open(fp, "w") as f:
                f.write(MANIFEST_WITH_NEWLINE)
            with open(fp, "r") as f:
                self.assertTrue(_check_newline(f))
            manifest = read_manifest(fp)
            dump_manifest(manifest, fp)
            with open(fp, "r") as f:
                self.assertTrue(_check_newline(f))

    def test_no_newline(self):
        with tempfile.TemporaryDirectory() as d:
            fp = os.path.join(d, "notrailingnewline.json")
            with open(fp, "w") as f:
                f.write(MANIFEST_NO_NEWLINE)
            with open(fp, "r") as f:
                self.assertFalse(_check_newline(f))
            manifest = read_manifest(fp)
            dump_manifest(manifest, fp)
            with open(fp, "r") as f:
                self.assertFalse(_check_newline(f))


if __name__ == "__main__":
    unittest.main()
