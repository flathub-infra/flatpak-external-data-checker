#!/usr/bin/env python3
# Copyright © 2020–2021 Maximiliano Sandoval <msandova@protonmail.com>
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
from distutils.version import LooseVersion

from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from src.lib.checksums import MultiDigest
from src.checkers.gnomechecker import _is_stable

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.gnome.baobab.json")


class TestGNOMEChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    def test_is_stable(self):
        self.assertFalse(_is_stable("1.9.0"))
        self.assertTrue(_is_stable("3.28.0"))
        self.assertFalse(_is_stable("3.29.0"))
        self.assertTrue(_is_stable("41"))
        self.assertTrue(_is_stable("41.1"))
        self.assertTrue(_is_stable("41.2"))
        self.assertTrue(_is_stable("4.1"))
        self.assertTrue(_is_stable("4.2"))
        self.assertFalse(_is_stable("4.rc"))
        self.assertFalse(_is_stable("4.2.beta"))
        self.assertFalse(_is_stable("4.alpha.0"))

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        for data in ext_data:
            self.assertIsNotNone(data.new_version)
            self.assertIsNotNone(data.new_version.checksum)
            self.assertIsInstance(data.new_version.checksum, MultiDigest)
            self.assertNotEqual(
                data.new_version.checksum,
                MultiDigest(
                    sha256="0000000000000000000000000000000000000000000000000000000000000000"
                ),
            )
            self.assertIsNotNone(data.new_version.version)
            self.assertIsInstance(data.new_version.version, str)

            if data.filename == "baobab-3.34.0.tar.xz":
                self._test_stable_only(data)
            elif data.filename == "pygobject-3.36.0.tar.xz":
                self._test_include_unstable(data)
                self.assertLess(
                    LooseVersion(data.new_version.version), LooseVersion("3.38.0")
                )
            elif data.filename == "alleyoop-0.9.8.tar.xz":
                self._test_non_standard_version(data)

    def _test_stable_only(self, data):
        self.assertEqual(data.filename, "baobab-3.34.0.tar.xz")
        self.assertRegex(
            data.new_version.url,
            r"^https://download\.gnome\.org/sources/baobab/.+/baobab-.+\.tar\.xz$",  # noqa: E501
        )

    def _test_include_unstable(self, data):
        self.assertEqual(data.filename, "pygobject-3.36.0.tar.xz")
        self.assertRegex(
            data.new_version.url,
            r"^https://download\.gnome\.org/sources/pygobject/.+/pygobject-.+\.tar\.xz$",  # noqa: E501
        )

    def _test_non_standard_version(self, data):
        self.assertEqual(data.filename, "alleyoop-0.9.8.tar.xz")
        self.assertEqual(
            data.new_version.version,
            "0.9.8",
        )


if __name__ == "__main__":
    unittest.main()
