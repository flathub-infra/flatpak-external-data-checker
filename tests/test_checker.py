# Copyright (C) 2018 Endless Mobile, Inc.
#
# Authors:
#       Joaquim Rocha <jrocha@endlessm.com>
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
import sys
import unittest

tests_dir = os.path.dirname(__file__)
checker_path = os.path.join(tests_dir, '..', 'src')
sys.path.append(checker_path)

from lib.externaldata import ExternalData
import checker

TEST_MANIFEST = os.path.join(tests_dir, 'org.externaldatachecker.Manifest.json')

class TestExternalDataChecker(unittest.TestCase):

    def setUp(self):
        logging.basicConfig(level=logging.DEBUG)
        self.checker = checker.ManifestChecker(TEST_MANIFEST)

    def test_check(self):
        self.checker.check()
        ext_data = self.checker.get_external_data()

        self.assertEqual(len(ext_data), 5)

        ext_data_with_new_version = 0

        for data in ext_data:
            if data.new_version:
                ext_data_with_new_version += 1

        self.assertEqual(ext_data_with_new_version, 3)

        file_ext_data = self.checker.get_external_data(ExternalData.Type.FILE)
        self.assertEqual(len(file_ext_data), 1)

        archive_ext_data = self.checker.get_external_data(ExternalData.Type.ARCHIVE)
        self.assertEqual(len(archive_ext_data), 1)

        extra_data = self.checker.get_external_data(ExternalData.Type.EXTRA_DATA)
        self.assertEqual(len(extra_data), 3)

        outdated_ext_data = self.checker.get_outdated_external_data()
        self.assertEqual(len(outdated_ext_data), 5)

if __name__ == '__main__':
    unittest.main()
