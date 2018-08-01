# Rotating URL Checker: A checker that verifies generic links that
# redirect to a versioned archive that changes, e.g.:
#    http://example.com/last-version -> http://example.com/prog_1.2.3.gz
#
# It uses some metadata in the manifest file for knowing where to look:
#
# The contens of the x-checker-data for the module should be .e.g:
#   "x-checker-data": {
#                       "type": "rotating-url",
#                       "url": "http://example.com/last-version"
#                     }
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

from lib.externaldata import ExternalData, CheckerRegistry, Checker
from lib import utils


class RotatingURLChecker(Checker):

    def _should_check(self, external_data):
        return external_data.checker_data and \
               external_data.checker_data.get('type') == 'rotating-url'

    def check(self, external_data):
        # Only process external data of the rotating-url
        if not self._should_check(external_data):
            logging.debug('%s is not a rotating-url type ext data', external_data.filename)
            return

        url = external_data.checker_data['url']
        logging.debug('Getting extra data info from URL %s ; may take a '
                      'while', url)
        try:
            new_url, checksum, size = utils.get_extra_data_info_from_url(url)
        except Exception as e:
            logging.debug(e)
            return

        if checksum == external_data.checksum:
            logging.debug('URL %s still valid', url)
            return

        new_ext_data = ExternalData(external_data.type,
                                    external_data.filename,
                                    new_url, checksum, size,
                                    external_data.arches)
        new_ext_data.checker_data = external_data.checker_data
        external_data.new_version = new_ext_data


CheckerRegistry.register_checker(RotatingURLChecker)
