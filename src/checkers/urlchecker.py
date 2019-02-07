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

from lib.externaldata import ExternalData, CheckerRegistry, Checker
from lib import utils

log = logging.getLogger(__name__)


class URLChecker(Checker):
    def check(self, external_data):
        url = external_data.current_version.url
        log.debug('Checking %s is reachable', url)
        try:
            utils.check_url_reachable(url)
        except urllib.error.HTTPError as e:
            log.warning('%s returned %s', url, e)
            external_data.state = ExternalData.State.BROKEN
        except:
            log.exception('Unexpected exception while checking %s',
                          url, exc_info=True)
            external_data.state = ExternalData.State.BROKEN
        else:
            external_data.state = ExternalData.State.VALID


CheckerRegistry.register_checker(URLChecker)
