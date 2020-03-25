#!/usr/bin/env python3
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

import datetime as dt
import logging
import os
import unittest
import tempfile

from xml.dom import minidom

from src.lib.utils import init_logging
from src.lib.externaldata import ExternalData, Checker
from src.checker import ManifestChecker

TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__), "org.externaldatachecker.Manifest.json",
)
NUM_ARCHIVE_IN_MANIFEST = 1
NUM_FILE_IN_MANIFEST = 1
NUM_EXTRA_DATA_IN_MANIFEST = 6
NUM_ALL_EXT_DATA = (
    NUM_ARCHIVE_IN_MANIFEST + NUM_FILE_IN_MANIFEST + NUM_EXTRA_DATA_IN_MANIFEST
)


class DummyChecker(Checker):
    def check(self, external_data):
        logging.debug(
            "Phony checker checking external data %s and all is always good",
            external_data.filename,
        )


class UpdateEverythingChecker(Checker):
    SIZE = 0
    # echo -n | sha256sum
    CHECKSUM = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    VERSION = "1.2.3.4"
    TIMESTAMP = dt.datetime(2019, 8, 28, 0, 0, 0)

    def check(self, external_data):
        external_data.state = ExternalData.State.BROKEN
        external_data.new_version = external_data.current_version._replace(
            size=self.SIZE,
            checksum=self.CHECKSUM,
            version=self.VERSION,
            timestamp=self.TIMESTAMP,
        )


class RemoveEverythingChecker(Checker):
    def check(self, external_data):
        external_data.state = ExternalData.State.REMOVED


class TestExternalDataChecker(unittest.TestCase):
    def setUp(self):
        init_logging()

    def test_check_filtered(self):
        # Use only the URLChecker which is fast so we don't have to wait a lot
        # for this test; so save the real checkers for later
        dummy_checker = ManifestChecker(TEST_MANIFEST)
        dummy_checker._checkers = [DummyChecker()]

        ext_data = dummy_checker.check()
        ext_data_from_getter = dummy_checker.get_external_data()
        self.assertEqual(len(ext_data), len(ext_data_from_getter))
        self.assertEqual(set(ext_data), set(ext_data_from_getter))

        self.assertEqual(len(ext_data), NUM_ALL_EXT_DATA)
        ext_data = dummy_checker.check(ExternalData.Type.EXTRA_DATA)
        self.assertEqual(len(ext_data), NUM_EXTRA_DATA_IN_MANIFEST)

        ext_data = dummy_checker.check(ExternalData.Type.FILE)
        self.assertEqual(len(ext_data), NUM_FILE_IN_MANIFEST)

        ext_data = dummy_checker.check(ExternalData.Type.ARCHIVE)
        self.assertEqual(len(ext_data), NUM_ARCHIVE_IN_MANIFEST)

    def _test_update(self, filename, contents, expected_new_contents, expected_updates):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = os.path.join(tmpdir, filename)
            with open(manifest, "w") as f:
                f.write(contents)

            appdata = os.path.join(
                tmpdir, os.path.splitext(filename)[0] + ".appdata.xml",
            )
            with open(appdata, "w") as f:
                f.write("""<application></application>""")

            checker = ManifestChecker(manifest)
            checker._checkers = [UpdateEverythingChecker()]
            checker.check()
            updates = checker.update_manifests()

            with open(manifest, "r") as f:
                new_contents = f.read()

            self.assertEqual(new_contents, expected_new_contents)
            self.assertEqual(updates, expected_updates)

            with open(appdata, "r") as f:
                appdata_doc = minidom.parse(f)

            releases = appdata_doc.getElementsByTagName("release")
            self.assertNotEqual(releases, [])
            self.assertEqual(releases, releases[:1])
            self.assertEqual(releases[0].getAttribute("version"), "1.2.3.4")
            self.assertEqual(releases[0].getAttribute("date"), "2019-08-28")

    def test_update_json(self):
        filename = "com.example.App.json"
        contents = """
/* unfortunately we can't or won't preserve C-style comments */
{
    "modules": [
        {
            "name": "foo",
            "sources": [
                {
                    "type": "extra-data",
                    "filename": "UnityHubSetup.AppImage",
                    "url": "https://public-cdn.cloud.unity3d.com/hub/prod/UnityHubSetup.AppImage",
                    "sha256": "c521e2caf2ce8c8302cc9d8f385648c7d8c76ae29ac24ec0c0ffd3cd67a915fc",
                    "size": 63236599
                }
            ]
        }
    ]
    }"""  # noqa: E501
        expected_new_contents = f"""
{{
    "modules": [
        {{
            "name": "foo",
            "sources": [
                {{
                    "type": "extra-data",
                    "filename": "UnityHubSetup.AppImage",
                    "url": "https://public-cdn.cloud.unity3d.com/hub/prod/UnityHubSetup.AppImage",
                    "sha256": "{UpdateEverythingChecker.CHECKSUM}",
                    "size": {UpdateEverythingChecker.SIZE}
                }}
            ]
        }}
    ]
}}""".lstrip()  # noqa: E501

        self._test_update(
            filename,
            contents,
            expected_new_contents,
            ["Update UnityHubSetup.AppImage to 1.2.3.4"],
        )

    def test_update_yaml(self):
        filename = "com.example.App.yaml"
        contents = """
modules:
  - name: the-blank-line-below-should-be-preserved

  - name: foo
    sources:
      - type: extra-data                  # Cool comments
        filename: UnityHubSetup.AppImage  # Very nice
        url: https://public-cdn.cloud.unity3d.com/hub/prod/UnityHubSetup.AppImage
        sha256: c521e2caf2ce8c8302cc9d8f385648c7d8c76ae29ac24ec0c0ffd3cd67a915fc
        size: 63236599
        only-arches:
          - i386

      - type: extra-data                  # Cool comments
        filename: UnityHubSetup.AppImage  # Very nice
        url: https://public-cdn.cloud.unity3d.com/hub/prod/UnityHubSetup.AppImage
        sha256: c521e2caf2ce8c8302cc9d8f385648c7d8c76ae29ac24ec0c0ffd3cd67a915fc
        size: 63236599
        only-arches:
          - x86_64
""".lstrip()
        expected_new_contents = f"""
modules:
  - name: the-blank-line-below-should-be-preserved

  - name: foo
    sources:
      - type: extra-data                  # Cool comments
        filename: UnityHubSetup.AppImage  # Very nice
        url: https://public-cdn.cloud.unity3d.com/hub/prod/UnityHubSetup.AppImage
        sha256: {UpdateEverythingChecker.CHECKSUM}
        size: {UpdateEverythingChecker.SIZE}
        only-arches:
          - i386

      - type: extra-data                  # Cool comments
        filename: UnityHubSetup.AppImage  # Very nice
        url: https://public-cdn.cloud.unity3d.com/hub/prod/UnityHubSetup.AppImage
        sha256: {UpdateEverythingChecker.CHECKSUM}
        size: {UpdateEverythingChecker.SIZE}
        only-arches:
          - x86_64
""".lstrip()
        self._test_update(
            filename,
            contents,
            expected_new_contents,
            ["Update UnityHubSetup.AppImage to 1.2.3.4"],
        )

    def _test_remove(self, filename, contents, expected_new_contents, expected_updates):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = os.path.join(tmpdir, filename)
            with open(manifest, "w") as f:
                f.write(contents)

            checker = ManifestChecker(manifest)
            checker._checkers = [RemoveEverythingChecker()]
            checker.check()
            updates = checker.update_manifests()

            with open(manifest, "r") as f:
                new_contents = f.read()

            self.assertEqual(new_contents, expected_new_contents)
            self.assertEqual(updates, expected_updates)

    def test_remove_json(self):
        filename = "com.example.App.json"
        contents = """
{
    "modules": [
        {
            "name": "foo",
            "sources": [
                {
                    "type": "extra-data",
                    "url": "https://example.com/foo-bar.xpi",
                    "sha256": "c521e2caf2ce8c8302cc9d8f385648c7d8c76ae29ac24ec0c0ffd3cd67a915fc",
                    "size": 63236599
                }
            ]
        }
    ]
}"""  # noqa: E501
        expected_new_contents = """
{
    "modules": [
        {
            "name": "foo",
            "sources": []
        }
    ]
}""".lstrip()  # noqa: E501

        self._test_remove(
            filename, contents, expected_new_contents, ["Removed foo-bar.xpi"],
        )

    def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = checker.check()

        self.assertEqual(len(ext_data), NUM_ALL_EXT_DATA)

        ext_data_with_new_version = 0

        for data in ext_data:
            if data.new_version:
                ext_data_with_new_version += 1

        self.assertEqual(ext_data_with_new_version, 4)

        file_ext_data = checker.get_external_data(ExternalData.Type.FILE)
        self.assertEqual(len(file_ext_data), NUM_FILE_IN_MANIFEST)

        archive_ext_data = checker.get_external_data(ExternalData.Type.ARCHIVE)
        self.assertEqual(len(archive_ext_data), NUM_ARCHIVE_IN_MANIFEST)

        extra_data = checker.get_external_data(ExternalData.Type.EXTRA_DATA)
        self.assertEqual(len(extra_data), NUM_EXTRA_DATA_IN_MANIFEST)

        outdated_ext_data = checker.get_outdated_external_data()
        self.assertEqual(len(outdated_ext_data), NUM_ALL_EXT_DATA - 1)

        dropbox = self._find_by_filename(ext_data, "dropbox.tgz")
        self.assertIsNotNone(dropbox)
        self.assertEqual(dropbox.new_version.version, "64")
        self.assertEqual(dropbox.new_version.url, "https://httpbin.org/base64/4puE")

        # this URL is a redirect, but since it is not a rotating-url the URL
        # should not be updated.
        image = self._find_by_filename(ext_data, "image.jpeg")
        self.assertIsNone(image)

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        else:
            return None


if __name__ == "__main__":
    unittest.main()
