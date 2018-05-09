# Copyright (C) 2018 Endless Mobile, Inc.
#
# Authors:
#       Andrew Hayzen <ahayzen@gmail.com>
#       Joaquim Rocha <jrocha@endlessm.com>
#       Patrick Griffis <tingping@tingping.se>
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
import re

from lib.externaldata import ExternalData, CheckerRegistry, Checker


class CVEChecker(Checker):

    def check(self, external_data):
        try:
            version = CVEChecker.extract_version_from_url(
                external_data.url, external_data.type,
            )
            logging.debug('CVEChecker: Found %s of the version %s' %
                          (external_data.pkg_name, version))
        except ValueError:
            external_data.state = ExternalData.State.BROKEN
        else:
            external_data.state = ExternalData.State.VALID

        # TODO: need similar to new_version but for cve_vuln
        # this should also output to JSON

    @staticmethod
    def extract_version_from_url(url, data_type):
        if data_type == ExternalData.Type.ARCHIVE:
            filename = url.rpartition('/')[2]
            match = re.search(r'(\d+\.\d+(?:\.\d+)?)', filename)

            if match:
                return match.groups()[-1]
            else:
                logging.debug('CVEChecker: Version not found in {}'.format(url))
                raise ValueError
        else:
            logging.debug('CVEChecker: Unknown type %s' % data_type)
            raise ValueError


CheckerRegistry.register_checker(CVEChecker)
