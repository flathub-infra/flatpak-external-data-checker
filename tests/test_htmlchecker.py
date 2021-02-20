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
from distutils.version import LooseVersion

from src.lib.utils import init_logging
from src.checker import ManifestChecker

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.x.xeyes.yml")


class TestHTMLChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()
        self._test_separate_patterns(
            self._find_by_filename(ext_data, "xeyes-1.1.0.tar.bz2")
        )
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

    def _test_separate_patterns(self, data):
        self.assertIsNotNone(data)
        self.assertRegex(data.filename, r"xeyes-[\d\.-]+.tar.bz2")
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https?://www.x.org/releases/individual/app/xeyes-[\d\.-]+.tar.bz2",  # noqa: E501
        )
        self.assertNotEqual(data.new_version.url, data.current_version.url)
        self.assertIsNotNone(data.new_version.version)
        self.assertLessEqual(
            LooseVersion("1.1.0"), LooseVersion(data.new_version.version)
        )
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, str)
        self.assertNotEqual(
            data.new_version.checksum,
            "0000000000000000000000000000000000000000000000000000000000000000",
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
        self.assertIsInstance(data.new_version.checksum, str)
        self.assertEqual(
            data.new_version.checksum,
            "d73b62f29eb98d850f16b76d759395180b860b613fbe1686b18eee99a6e3773f",
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
            "0000000000000000000000000000000000000000000000000000000000000000",
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
            r"^https://[\d\w\.-]+sourceforge.net/project/qrupdate/qrupdate/[\d\.]+/qrupdate-[\d\.]+.tar.gz$",  # noqa: E501
        )
        self.assertNotEqual(
            data.new_version.checksum,
            "0000000000000000000000000000000000000000000000000000000000000000",
        )

    def _test_no_match(self, data):
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
