# Flash Checker: A checker that to see if the url is pointing to the latest Flash Player.
#
# Consult the README for information on how to use this checker.
#
# Copyright © 2019 Endless Mobile, Inc.
#
# Authors:
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

import json
import logging
import urllib.error
import urllib.request
import urllib.parse

from lib.externaldata import ExternalData, Checker
from lib import utils

log = logging.getLogger(__name__)


FLASH_BASE_URL = 'http://get.adobe.com/flashplayer/webservices/json/?{params}'

FLATPAK_TO_FLASH_ARCH_MAP = {
    'i386': 'x86-32',
    'x86_64': 'x86-64',
}

BROWSER_TO_PAPI_MAP = {
    'Chrome': 'ppapi',
    'Firefox': 'npapi',
}


class FlashChecker(Checker):
    def _should_check(self, external_data):
        return external_data.checker_data.get('type') == 'flash'

    def _get_arches(self, external_data):
        if len(external_data.arches) != 1:
            return None

        flatpak_arch = external_data.arches[0]
        return flatpak_arch, FLATPAK_TO_FLASH_ARCH_MAP.get(external_data.arches[0])

    def check(self, external_data):
        if not self._should_check(external_data):
            log.debug('%s is not a flash type ext data', external_data.filename)
            return

        browser = external_data.checker_data['browser'].title()

        try:
            papi = BROWSER_TO_PAPI_MAP[browser]
        except KeyError:
            log.warning('%s has an invalid browser (should be one of %s)',
                        external_data.filename, ', '.join(BROWSER_TO_PAPI_MAP))
            return

        arches = self._get_arches(external_data)
        if arches is None:
            log.warning('%s has invalid only-arches (should be one of %s)',
                        external_data.filename,
                        ', '.join(f'[{arch}]' for arch in FLATPAK_TO_FLASH_ARCH_MAP))
            return

        flatpak_arch, flash_arch = arches

        request_params = {'platform_type': 'Linux', 'eventname': 'flashplayerotherversions',
                          'platform_arch': flash_arch}
        request_url = FLASH_BASE_URL.format(params=urllib.parse.urlencode(request_params))
        with urllib.request.urlopen(request_url) as resp:
            latest_version_data = json.load(resp)

        latest_version = None
        latest_url = None

        for version in latest_version_data:
            if (version['installation_type'] == 'Standalone' and version['browser'] == browser
                    and version['installer_architecture'] == flash_arch):
                assert version['platform'] == 'Linux'

                latest_version = version['Version']
                latest_url = version['download_url']
                break
        else:
            log.warning('%s had no available URL', external_data.filename)
            return

        assert latest_version is not None
        assert latest_url is not None

        try:
            new_version, _ = utils.get_extra_data_info_from_url(latest_url)
        except urllib.error.HTTPError as e:
            log.warning('%s returned %s', latest_url, e)
            external_data.state = ExternalData.State.BROKEN
        except Exception:
            log.exception('Unexpected exception while checking %s', latest_url)
            external_data.state = ExternalData.State.BROKEN
        else:
            external_data.state = ExternalData.State.VALID
            new_version = new_version._replace(version=latest_version)
            if not external_data.current_version.matches(new_version):
                external_data.new_version = new_version
