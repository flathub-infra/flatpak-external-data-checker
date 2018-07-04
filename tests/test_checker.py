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
import unittest

from lib.externaldata import ExternalData, Checker
import checker

TESTS_DIR = os.path.dirname(__file__)
TEST_MANIFEST = os.path.join(TESTS_DIR, 'org.externaldatachecker.Manifest.json')
NUM_ARCHIVE_IN_MANIFEST = 1
NUM_FILE_IN_MANIFEST = 1
NUM_EXTRA_DATA_IN_MANIFEST = 5
NUM_ALL_EXT_DATA = NUM_ARCHIVE_IN_MANIFEST + NUM_FILE_IN_MANIFEST + \
                   NUM_EXTRA_DATA_IN_MANIFEST


class DummyChecker(Checker):

    def check(self, external_data):
        logging.debug('Phony checker checking external data %s and all is always good',
                      external_data.filename)


class TestExternalDataChecker(unittest.TestCase):

    def setUp(self):
        logging.basicConfig(level=logging.DEBUG)
        self.checker = checker.ManifestChecker(TEST_MANIFEST)

    def test_check_filtered(self):
        # Use only the URLChecker which is fast so we don't have to wait a lot
        # for this test; so save the real checkers for later
        dummy_checker = checker.ManifestChecker(TEST_MANIFEST)
        dummy_checker._checkers = [DummyChecker()]

        ext_data = dummy_checker.check()
        ext_data_from_getter = dummy_checker.get_external_data()
        self.assertEqual(ext_data_from_getter, ext_data)

        self.assertEqual(len(ext_data), NUM_ALL_EXT_DATA)
        ext_data = dummy_checker.check(ExternalData.Type.EXTRA_DATA)
        self.assertEqual(len(ext_data), NUM_EXTRA_DATA_IN_MANIFEST)

        ext_data = dummy_checker.check(ExternalData.Type.FILE)
        self.assertEqual(len(ext_data), NUM_FILE_IN_MANIFEST)

        ext_data = dummy_checker.check(ExternalData.Type.ARCHIVE)
        self.assertEqual(len(ext_data), NUM_ARCHIVE_IN_MANIFEST)

    def test_check(self):
        ext_data = self.checker.check()

        self.assertEqual(len(ext_data), NUM_ALL_EXT_DATA)

        ext_data_with_new_version = 0

        for data in ext_data:
            if data.new_version:
                ext_data_with_new_version += 1

        self.assertEqual(ext_data_with_new_version, 4)

        file_ext_data = self.checker.get_external_data(ExternalData.Type.FILE)
        self.assertEqual(len(file_ext_data), NUM_FILE_IN_MANIFEST)

        archive_ext_data = self.checker.get_external_data(ExternalData.Type.ARCHIVE)
        self.assertEqual(len(archive_ext_data), NUM_ARCHIVE_IN_MANIFEST)

        extra_data = self.checker.get_external_data(ExternalData.Type.EXTRA_DATA)
        self.assertEqual(len(extra_data), NUM_EXTRA_DATA_IN_MANIFEST)

        outdated_ext_data = self.checker.get_outdated_external_data()
        self.assertEqual(len(outdated_ext_data), NUM_ALL_EXT_DATA)


if __name__ == '__main__':
    unittest.main()
