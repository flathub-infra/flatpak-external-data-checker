# URL Checker: A simple checker that just verifies if an external data
# URL is still accessible. Does not need an x-checker-data entry and
# works with all external data types (that have a URL).
#
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
import urllib.error

from lib.externaldata import ExternalData, ExternalFile, Checker
from lib import utils

log = logging.getLogger(__name__)


class URLChecker(Checker):
    def check(self, external_data):
        url = external_data.current_version.url
        log.debug("Checking %s has expected size and checksum", url)

        try:
            # Ignore any redirect, since many URLs legitimately get redirected
            # to mirrors
            _, checksum, size = utils.get_extra_data_info_from_url(url)
        except urllib.error.HTTPError as e:
            log.warning('%s returned %s', url, e)
            external_data.state = ExternalData.State.BROKEN
        except Exception:
            log.exception('Unexpected exception while checking %s', url)
            external_data.state = ExternalData.State.BROKEN
        else:
            new_version = ExternalFile(url, checksum, size)
            if external_data.current_version.matches(new_version):
                external_data.state = ExternalData.State.VALID
            else:
                external_data.state = ExternalData.State.BROKEN
                external_data.new_version = new_version
