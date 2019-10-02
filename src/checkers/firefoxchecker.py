# Firefox Checker: A checker that uses some metadata info from the manifest
# file in order to check whether there are newer versions of Firefox external
# data modules. It will also add and remove translations as available
# by upstream.
#
# Consult the README for information on how to use this checker.
#
# Copyright © 2019 Endless Mobile, Inc.
#
# Authors:
#       Andre Magalhaes <andre@endlessm.com>
#       Ryan Gonzalez <rymg19@gmail.com>
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

import functools
import json
import logging
import os
import re
import urllib.request

from src.lib.externaldata import ExternalData, ExternalDataSource, Checker
from src.lib import utils

log = logging.getLogger(__name__)


class FirefoxChecker(Checker):
    FIREFOX_RELEASE_INFO_URL = 'https://product-details.mozilla.org/1.0/firefox_versions.json'  # noqa: E501
    FIREFOX_ARCHIVE_BASE_URL = 'https://archive.mozilla.org/pub/firefox/releases/{version}/'  # noqa: E501
    FIREFOX_PLATFORM = 'linux-x86_64'
    CHECKER_BROWSER_SOURCE_FILENAME = 'firefox.tar.bz2'
    CHECKER_BROWSER_FILENAME_MATCH = r'firefox-.+\.tar\.bz2$'
    CHECKER_BROWSER_DEFAULT_LANGUAGE = 'en-US'
    CHECKER_TRANSLATIONS_FILENAME_MATCH = r'.+\.xpi$'

    def _should_check(self, module_data):
        return module_data.checker_data.get('type') == 'firefox'

    def check_module(self, module_data, external_data):
        if not self._should_check(module_data):
            return

        log.debug('Retrieving latest available firefox version')
        # This should raise an error if either the release info url or
        # the returned json data is broken. Lets not catch it here as
        # we want that checker to break if that happens.
        latest_version = self._get_latest_available_version()
        assert latest_version is not None

        log.debug('Latest available firefox version: %s', latest_version)

        log.debug('Retrieving latest firefox version info')
        latest_info = self._get_latest_version_info(latest_version)

        log.debug('Checking external data')
        browser_data = None
        for data in external_data:
            if re.match(self.CHECKER_BROWSER_SOURCE_FILENAME, data.filename):
                browser_data = data
                break

        if not browser_data:
            log.warning('Unable to find browser source with filename %s',
                        module_data.name,
                        self.CHECKER_BROWSER_SOURCE_FILENAME)
            return None

        added = []
        processed_data = []
        for data in external_data:
            if data.filename in latest_info:
                new_version = latest_info[data.filename]
                data.state = ExternalData.State.VALID
                if not data.current_version.matches(new_version):
                    data.new_version = new_version
            else:
                if re.match(self.CHECKER_BROWSER_FILENAME_MATCH, data.filename) or \
                   re.match(self.CHECKER_TRANSLATIONS_FILENAME_MATCH, data.filename):
                    data.state = ExternalData.State.REMOVED
            processed_data.append(data.filename)

        added = []
        for source_filename, info in latest_info.items():
            if source_filename not in processed_data:
                source = {
                    'filename': source_filename,
                    'type': 'extra-data',
                    'url': info.url,
                    'sha256': info.checksum,
                    'size': info.size,
                }
                # add new data to the same manifest as the main browser tarball
                data = ExternalDataSource.from_source(
                    browser_data.source_path,
                    source,
                    browser_data.source_parent,
                )
                data.state = ExternalData.State.ADDED
                added.append(data)

        return added

    @functools.lru_cache()
    def _get_latest_available_version(self):
        latest_version_data = {}
        request_url = self.FIREFOX_RELEASE_INFO_URL
        with urllib.request.urlopen(request_url) as resp:
            latest_version_data = json.load(resp)

        return latest_version_data['LATEST_FIREFOX_VERSION']

    def _get_latest_version_info(self, version):
        results = {}

        base_url = self.FIREFOX_ARCHIVE_BASE_URL.format(version=version)

        url = '{}SHA256SUMS'.format(base_url)
        with urllib.request.urlopen(url) as response:
            sha256_table = response.read().decode()

        for line in sha256_table.splitlines():
            sha256, path = line.split(None, 1)
            path = path.strip()

            if path.startswith(self.FIREFOX_PLATFORM):
                assert path.count('/') == 2, path
                _, dirname, filename = path.split('/')

                source_filename = None
                if re.match(self.CHECKER_BROWSER_FILENAME_MATCH, filename) and \
                   dirname == self.CHECKER_BROWSER_DEFAULT_LANGUAGE:
                    source_filename = self.CHECKER_BROWSER_SOURCE_FILENAME
                elif re.match(self.CHECKER_TRANSLATIONS_FILENAME_MATCH, filename):
                    source_filename = os.path.basename(filename)

                if not source_filename:
                    continue

                url = '{}{}'.format(base_url, path)
                log.debug("Inspecting %s", url)
                # Unfortunately, the release date in firefox_versions.json does not
                # seem to be updated when a point release is made, so we have to get it
                # from the files' last-modified date.
                #
                # TODO: just make a HEAD request to get the date and size, and fill in
                # the SHA256sum that we already know.
                info, _ = utils.get_extra_data_info_from_url(url)
                info = info._replace(version=version)
                results[source_filename] = info

        return results
