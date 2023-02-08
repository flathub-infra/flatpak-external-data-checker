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
import hashlib
import base64
from xml.dom import minidom
import typing as t

import aiohttp

from src.lib.utils import init_logging
from src.lib.externaldata import ExternalData
from src.checkers import Checker
from src.checkers.gitchecker import GitChecker
from src.lib.externaldata import ExternalGitRepo
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerFetchError
from src import manifest

TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__),
    "org.externaldatachecker.Manifest.json",
)
NUM_ARCHIVE_IN_MANIFEST = 1
NUM_FILE_IN_MANIFEST = 1
NUM_EXTRA_DATA_IN_MANIFEST = 11
NUM_ALL_EXT_DATA = (
    NUM_ARCHIVE_IN_MANIFEST + NUM_FILE_IN_MANIFEST + NUM_EXTRA_DATA_IN_MANIFEST
)
NUM_UP_TO_DATE_DATA = 2
NUM_SKIPPED_DATA = 2
NUM_OUTDATED_DATA = NUM_ALL_EXT_DATA - (NUM_UP_TO_DATE_DATA + NUM_SKIPPED_DATA)
NUM_NEW_VERSIONS = 2


class DummyChecker(Checker):
    def get_json_schema(self, external_data):
        return None

    @classmethod
    def should_check(cls, external_data):
        return True

    async def check(self, external_data):
        logging.debug(
            "Phony checker checking external data %s and all is always good",
            external_data.filename,
        )


class UpdateEverythingChecker(DummyChecker):
    SIZE = 0
    # echo -n | sha256sum
    CHECKSUM = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    VERSION = "1.2.3.4"
    TIMESTAMP = dt.datetime(2019, 8, 28, 0, 0, 0)

    async def check(self, external_data):
        external_data.state |= external_data.State.BROKEN
        external_data.new_version = external_data.current_version._replace(
            size=self.SIZE,
            checksum=MultiDigest(sha256=self.CHECKSUM),
            version=self.VERSION,
            timestamp=self.TIMESTAMP,
        )


class _TestWithInlineManifest(unittest.IsolatedAsyncioTestCase):
    _DUMMY_CHECKER_CLS: t.Type[Checker]
    maxDiff = None

    async def _test_update(
        self,
        filename,
        contents,
        expected_new_contents,
        expected_updates,
        expected_data_count=1,
        new_release=True,
        require_important_update=False,
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, filename)
            with open(manifest_path, "w") as f:
                f.write(contents)

            appdata = os.path.join(
                tmpdir,
                os.path.splitext(filename)[0] + ".appdata.xml",
            )
            with open(appdata, "w") as f:
                f.write("""<application></application>""")

            options = manifest.CheckerOptions(
                require_important_update=True,
            )

            checker = manifest.ManifestChecker(manifest_path, options)
            self.assertEqual(
                len(checker.get_external_data()),
                expected_data_count,
            )
            checker._checkers = [self._DUMMY_CHECKER_CLS]
            await checker.check()
            updates = checker.update_manifests()

            with open(manifest_path, "r") as f:
                new_contents = f.read()

            self.assertEqual(new_contents, expected_new_contents)
            self.assertEqual(updates, expected_updates)

            with open(appdata, "r") as f:
                appdata_doc = minidom.parse(f)

            releases = appdata_doc.getElementsByTagName("release")
            if new_release:
                self.assertNotEqual(releases, [])
                self.assertEqual(releases, releases[:1])
                self.assertEqual(releases[0].getAttribute("version"), "1.2.3.4")
                self.assertEqual(releases[0].getAttribute("date"), "2019-08-28")
            else:
                self.assertEqual(releases, [])


class TestExternalDataChecker(_TestWithInlineManifest):
    _DUMMY_CHECKER_CLS = UpdateEverythingChecker

    def setUp(self):
        init_logging()

    async def test_check_filtered(self):
        # Use only the URLChecker which is fast so we don't have to wait a lot
        # for this test; so save the real checkers for later
        dummy_checker = manifest.ManifestChecker(TEST_MANIFEST)
        dummy_checker._checkers = [DummyChecker]

        ext_data = await dummy_checker.check()
        ext_data_from_getter = dummy_checker.get_external_data()
        self.assertEqual(len(ext_data), len(ext_data_from_getter))
        self.assertEqual(ext_data, ext_data_from_getter)

        self.assertEqual(len(ext_data), NUM_ALL_EXT_DATA)
        ext_data = await dummy_checker.check(ExternalData.Type.EXTRA_DATA)
        self.assertEqual(len(ext_data), NUM_EXTRA_DATA_IN_MANIFEST)

        ext_data = await dummy_checker.check(ExternalData.Type.FILE)
        self.assertEqual(len(ext_data), NUM_FILE_IN_MANIFEST)

        ext_data = await dummy_checker.check(ExternalData.Type.ARCHIVE)
        self.assertEqual(len(ext_data), NUM_ARCHIVE_IN_MANIFEST)

    async def test_update_json(self):
        filename = "com.example.App.json"
        contents = """
/* unfortunately we can't or won't preserve C-style comments */
{
    "app-id": "com.example.App",
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
    "app-id": "com.example.App",
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

        await self._test_update(
            filename,
            contents,
            expected_new_contents,
            ["Update UnityHubSetup.AppImage to 1.2.3.4"],
        )

    async def test_update_yaml(self):
        filename = "com.example.App.yaml"
        contents = """
id: com.example.App
modules:
  - name: the-blank-line-below-should-be-preserved

  - name: foo
    # Anchor
    sources: &foo-sources
      - &foo-i386
        type: extra-data                  # Cool comments
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

      - *foo-i386

  - name: foo-32bit
    # Alias
    sources: *foo-sources
""".lstrip()
        expected_new_contents = f"""
id: com.example.App
modules:
  - name: the-blank-line-below-should-be-preserved

  - name: foo
    # Anchor
    sources: &foo-sources
      - &foo-i386
        type: extra-data                  # Cool comments
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

      - *foo-i386

  - name: foo-32bit
    # Alias
    sources: *foo-sources
""".lstrip()
        await self._test_update(
            filename,
            contents,
            expected_new_contents,
            ["Update UnityHubSetup.AppImage to 1.2.3.4"],
            expected_data_count=2,
        )

    async def test_update_no_new_version(self):
        filename = "com.example.App.yaml"
        contents = """
id: com.example.App
modules:
  - name: foo
    sources:
      - type: extra-data
        filename: some-deb.deb
        url: https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb
        sha256: c521e2caf2ce8c8302cc9d8f385648c7d8c76ae29ac24ec0c0ffd3cd67a915fc
        size: 63236599
""".lstrip()
        expected_new_contents = f"""
id: com.example.App
modules:
  - name: foo
    sources:
      - type: extra-data
        filename: some-deb.deb
        url: https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb
        sha256: {UpdateEverythingChecker.CHECKSUM}
        size: {UpdateEverythingChecker.SIZE}
""".lstrip()
        await self._test_update(
            filename,
            contents,
            expected_new_contents,
            ["Update some-deb.deb to 1.2.3.4"],
            new_release=False,
        )

    async def test_update_single_module(self):
        filename = "foo-module.yaml"
        contents = """
name: foo
sources:
  - type: extra-data
    filename: some-deb.deb
    url: https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb
    sha256: 0000000000000000000000000000000000000000000000000000000000000000
    size: 0
""".lstrip()
        expected_new_contents = f"""
name: foo
sources:
  - type: extra-data
    filename: some-deb.deb
    url: https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb
    sha256: {UpdateEverythingChecker.CHECKSUM}
    size: {UpdateEverythingChecker.SIZE}
""".lstrip()
        await self._test_update(
            filename,
            contents,
            expected_new_contents,
            ["Update some-deb.deb to 1.2.3.4"],
            new_release=False,
        )

    async def test_update_single_source(self):
        filename = "foo-source.yaml"
        contents = """
type: extra-data
filename: some-deb.deb
url: https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb
sha256: 0000000000000000000000000000000000000000000000000000000000000000
size: 0
""".lstrip()
        expected_new_contents = f"""
type: extra-data
filename: some-deb.deb
url: https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb
sha256: {UpdateEverythingChecker.CHECKSUM}
size: {UpdateEverythingChecker.SIZE}
""".lstrip()
        await self._test_update(
            filename,
            contents,
            expected_new_contents,
            ["Update some-deb.deb to 1.2.3.4"],
            new_release=False,
        )

    async def test_update_sources(self):
        filename = "foo-sources.json"
        contents = """
[
    {
        "type": "extra-data",
        "filename": "some-deb.deb",
        "url": "https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "size": 0
    },
    {
        "type": "extra-data",
        "filename": "some-deb.deb",
        "url": "https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb",
        "sha256": "0000000000000000000000000000000000000000000000000000000000000000",
        "size": 0
    }
]
""".lstrip()
        expected_new_contents = f"""
[
    {{
        "type": "extra-data",
        "filename": "some-deb.deb",
        "url": "https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb",
        "sha256": "{UpdateEverythingChecker.CHECKSUM}",
        "size": {UpdateEverythingChecker.SIZE}
    }},
    {{
        "type": "extra-data",
        "filename": "some-deb.deb",
        "url": "https://phony-url.phony/some-deb_1.2.3.4-1_amd64.deb",
        "sha256": "{UpdateEverythingChecker.CHECKSUM}",
        "size": {UpdateEverythingChecker.SIZE}
    }}
]
""".lstrip()
        await self._test_update(
            filename,
            contents,
            expected_new_contents,
            ["Update some-deb.deb to 1.2.3.4"],
            expected_data_count=2,
            new_release=False,
        )

    async def test_check(self):
        checker = manifest.ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        self.assertEqual(len(ext_data), NUM_ALL_EXT_DATA)

        ext_data_with_new_version = 0

        for data in ext_data:
            if data.new_version:
                self.assertIsInstance(data.new_version.url, str)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                self.assertIsInstance(data.new_version.size, int)
                ext_data_with_new_version += 1

        self.assertEqual(ext_data_with_new_version, NUM_NEW_VERSIONS)

        file_ext_data = checker.get_external_data(ExternalData.Type.FILE)
        self.assertEqual(len(file_ext_data), NUM_FILE_IN_MANIFEST)

        archive_ext_data = checker.get_external_data(ExternalData.Type.ARCHIVE)
        self.assertEqual(len(archive_ext_data), NUM_ARCHIVE_IN_MANIFEST)

        extra_data = checker.get_external_data(ExternalData.Type.EXTRA_DATA)
        self.assertEqual(len(extra_data), NUM_EXTRA_DATA_IN_MANIFEST)

        outdated_ext_data = checker.get_outdated_external_data()
        self.assertEqual(len(outdated_ext_data), NUM_OUTDATED_DATA)

        dropbox = self._find_by_filename(ext_data, "dropbox.tgz")
        self.assertIsNotNone(dropbox)
        self.assertEqual(dropbox.new_version.version, "64")
        self.assertEqual(dropbox.new_version.url, "https://httpbingo.org/base64/4puE")

        relative_redirect = self._find_by_filename(ext_data, "relative-redirect.txt")
        self.assertIsNotNone(relative_redirect)
        self.assertEqual(
            relative_redirect.new_version.url,
            "https://httpbingo.org/base64/MzAtNTAgZmVyYWwgaG9ncyEK",
        )
        self.assertEqual(
            relative_redirect.new_version.checksum,
            MultiDigest(
                sha256="e4d67702da4eeeb2f15629b65bf6767c028a511839c14ed44d9f34479eaa2b94"
            ),
        )
        self.assertEqual(relative_redirect.new_version.size, 18)

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


class TestCheckerHelpers(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

    async def asyncSetUp(self):
        self.http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10), raise_for_status=True
        )

    async def asyncTearDown(self):
        await self.http.close()

    @staticmethod
    def _get_url(data):
        return "https://httpbin.org/base64/" + base64.b64encode(data).decode("ascii")

    async def test_complete_digests(self):
        CORRECT = "Correct".encode()
        WRONG = "Wrong".encode()

        checker = DummyChecker(self.http)
        sha256 = hashlib.sha256(CORRECT).hexdigest()
        sha512 = hashlib.sha512(CORRECT).hexdigest()

        checksum = await checker._complete_digests(
            self._get_url(CORRECT), MultiDigest(sha512=sha512)
        )
        self.assertEqual(checksum.sha256, sha256)
        self.assertEqual(checksum.sha512, sha512)

        with self.assertRaises(CheckerFetchError):
            await checker._complete_digests(
                self._get_url(WRONG), MultiDigest(sha512=sha512)
            )

        with self.assertRaises(CheckerFetchError):
            await checker._complete_digests(
                "https://httpbin.org/base64/status/404", MultiDigest(sha512=sha512)
            )


class GitDummyChecker(GitChecker):
    def get_json_schema(self, external_data):
        return None

    @classmethod
    def should_check(cls, external_data):
        return True

    async def check(self, external_data):
        logging.debug(
            "Phony checker checking external data %s and all is always good",
            external_data.filename,
        )


class GitUpdateEverythingChecker(GitDummyChecker):
    # SIZE = 0
    # echo -n | sha256sum
    COMMIT = "abcdeadbeef00000000000000000000000000000"
    TAG = "1.2.3.4"
    TIMESTAMP = dt.date.fromisoformat("2019-08-28")

    async def check(self, external_data):
        external_data.state = ExternalGitRepo.State.UNKNOWN
        new_version = external_data.current_version._replace(
            commit=self.COMMIT,
            tag=self.TAG,
            version=self.TAG,
            timestamp=self.TIMESTAMP,
        )
        if not external_data.current_version.matches(new_version):
            external_data.new_version = new_version


class TestImportantGitExternalDataChecker(_TestWithInlineManifest):
    _DUMMY_CHECKER_CLS = GitUpdateEverythingChecker

    def setUp(self):
        init_logging()

    async def test_update_no_important_source_updated(self):
        """With two sources, while one source is updated it is not the important one,
        so no manifest update should be made."""
        filename = "importantsource.com.virustotal.Uploader.yml"
        contents = r"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^v(\d[\d.]+\d)$
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: 1.2.3.4
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true
          # since this is marked as the only important source, 
          # but isn't getting updated, manifest should not be updated
""".lstrip()
        expected_new_contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^v(\d[\d.]+\d)$
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: 1.2.3.4
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true
          # since this is marked as the only important source, 
          # but isn't getting updated, manifest should not be updated
""".lstrip()
        await self._test_update(
            filename=filename,
            contents=contents,
            expected_new_contents=expected_new_contents,
            expected_updates=[],
            expected_data_count=2,
            new_release=False,
            require_important_update=True,
        )

    async def test_update_one_important_source_updated(self):
        """With two sources, one of them being important and updated, so a manifest update should be made."""
        filename = "importantsource.com.virustotal.Uploader.yml"
        contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-important: true
          # since this is marked as the only important source, 
          # and is actually getting updated, manifest should be updated
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
""".lstrip()
        expected_new_contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-important: true
          # since this is marked as the only important source, 
          # and is actually getting updated, manifest should be updated
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
""".lstrip()
        await self._test_update(
            filename=filename,
            contents=contents,
            expected_new_contents=expected_new_contents,
            expected_updates=["Update extra-cmake-modules to 1.2.3.4"],
            expected_data_count=2,
            new_release=False,
            require_important_update=True,
        )

    async def test_update_two_important_sources_first_updated(self):
        """
        With two important sources, the first getting updated,
        so a manifest update should be made.
        Tests for correct looping (i.e. once it finds a singular important source that's updated it should update the manifest).
        """
        filename = "importantsource.com.virustotal.Uploader.yml"
        contents = r"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-important: true
          # since this is marked as a important source, 
          # and is getting updated, manifest should be updated
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: 1.2.3.4
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true 
          # this is marked as important and not being updated, 
          # yet the manifest should still be updated since the previous source is important and getting an update.
""".lstrip()
        expected_new_contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-important: true
          # since this is marked as a important source, 
          # and is getting updated, manifest should be updated
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true
          # this is marked as important and not being updated, 
          # yet the manifest should still be updated since the previous source is important and getting an update.
""".lstrip()
        await self._test_update(
            filename=filename,
            contents=contents,
            expected_new_contents=expected_new_contents,
            expected_updates=[
                "Update extra-cmake-modules to 1.2.3.4",
                "Update vt-py.git to 1.2.3.4",
            ],
            expected_data_count=2,
            new_release=False,
            require_important_update=True,
        )

    async def test_update_two_important_sources_second_updated(self):
        """With two important sources, the second getting updated,
        so a manifest update should be made.
        Tests for correct looping (i.e. once it finds a singular important source that's updated it should update the manifest).
        """
        filename = "importantsource.com.virustotal.Uploader.yml"
        contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true 
          # this is marked as important and not being updated, 
          # yet the manifest should still be updated since the next source is important and getting an update.
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-important: true
          # since this is marked as a important source, 
          # and is getting updated, manifest should be updated
""".lstrip()
        expected_new_contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true
          # this is marked as important and not being updated, 
          # yet the manifest should still be updated since the next source is important and getting an update.
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-important: true
          # since this is marked as a important source, 
          # and is getting updated, manifest should be updated
""".lstrip()
        await self._test_update(
            filename=filename,
            contents=contents,
            expected_new_contents=expected_new_contents,
            expected_updates=["Update extra-cmake-modules to 1.2.3.4"],
            expected_data_count=2,
            new_release=True,
            require_important_update=True,
        )

    async def test_update_no_main_source_updated(self):
        """With two sources, while one source is updated it is not the main one,
        so no manifest update should be made."""
        filename = "importantsource.com.virustotal.Uploader.yml"
        contents = r"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^v(\d[\d.]+\d)$
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: 1.2.3.4
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-main-source: true
          # since this is marked as the only important/main source,
          # but isn't getting updated, manifest should not be updated
""".lstrip()
        expected_new_contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^v(\d[\d.]+\d)$
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: 1.2.3.4
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-main-source: true
          # since this is marked as the only important/main source,
          # but isn't getting updated, manifest should not be updated
""".lstrip()
        await self._test_update(
            filename=filename,
            contents=contents,
            expected_new_contents=expected_new_contents,
            expected_updates=[],
            expected_data_count=2,
            new_release=False,
            require_important_update=True,
        )

    async def test_update_one_main_source_updated(self):
        """With two sources, one of them being the main source and updated,
        so a manifest update should be made."""
        filename = "importantsource.com.virustotal.Uploader.yml"
        contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-main-source: true
          # since this is marked as the only main source,
          # and is actually getting updated, manifest should be updated
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
""".lstrip()
        expected_new_contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-main-source: true
          # since this is marked as the only main source,
          # and is actually getting updated, manifest should be updated
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
""".lstrip()
        await self._test_update(
            filename=filename,
            contents=contents,
            expected_new_contents=expected_new_contents,
            expected_updates=["Update extra-cmake-modules to 1.2.3.4"],
            expected_data_count=2,
            new_release=True,
            require_important_update=True,
        )

    async def test_require_important_source_disabled_no_important_source_updated(self):
        """With two sources, one being important, but with require_important_source=false, so normal behaviour should occur.
        Should disregard the fact that there is a singular important source not being updated
        """
        filename = "importantsource.com.virustotal.Uploader.yml"
        contents = r"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^v(\d[\d.]+\d)$
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true
          # manifest should still be updated since the require_important source is disabled
""".lstrip()
        expected_new_contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^v(\d[\d.]+\d)$
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true
          # manifest should still be updated since the require_important source is disabled
""".lstrip()
        await self._test_update(
            filename=filename,
            contents=contents,
            expected_new_contents=expected_new_contents,
            expected_updates=[
                "Update extra-cmake-modules to 1.2.3.4",
                "Update vt-py.git to 1.2.3.4",
            ],
            expected_data_count=2,
            new_release=True,
            require_important_update=False,
        )

    async def test_main_source_not_important(self):
        """Normally the main source is considered important, however this can be overridden by setting is-important: false"""
        filename = "importantsource.com.virustotal.Uploader.yml"
        contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true
          # this is marked as important and not being updated
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-main-source: true
          is-important: false
          # since this is marked as the main source but is not important, it should not be updated
""".lstrip()
        expected_new_contents = rf"""
id: importantsource.com.virustotal.Uploader
modules:
  - name: vt-py
    sources:
      # Current is valid, no updates
      - type: git
        url: https://github.com/VirusTotal/vt-py.git
        tag: {GitUpdateEverythingChecker.TAG}
        commit: {GitUpdateEverythingChecker.COMMIT}
        x-checker-data:
          type: git
          tag-pattern: ^(0.5.4)$
          sort-tags: false
          is-important: true
          # this is marked as important and not being updated
  - name: extra-cmake-modules
    sources:
      - type: git
        url: https://github.com/KDE/extra-cmake-modules
        tag: 0.0.0.0
        commit: 0000000000000000000000000000000000000000
        x-checker-data:
          type: git
          tag-pattern: ^(v5\.90\.0)$ # to ensure we only get this version
          is-main-source: true
          is-important: false
          # since this is marked as the main source but is not important, it should not be updated
""".lstrip()
        await self._test_update(
            filename=filename,
            contents=contents,
            expected_new_contents=expected_new_contents,
            expected_updates=[],
            expected_data_count=2,
            new_release=False,
            require_important_update=True,
        )


if __name__ == "__main__":
    unittest.main()
