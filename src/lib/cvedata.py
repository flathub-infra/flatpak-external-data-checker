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

from collections import namedtuple
from operator import itemgetter
import re


class CVEData:
    @staticmethod
    def collect_cve_data(json_data):
        Library = namedtuple('Library', ('name', 'version'))
        libraries = []

        for module in json_data.get('modules', []):
            if type(module) is str:
                continue

            version = CVEData.extract_version(module.get('sources', []))

            if version:
                libraries.append(Library(module.get("name", None), version))

        libraries.sort(key=itemgetter(0))  # Sort by name

        return libraries

    @staticmethod
    def extract_version(sources):
        for source in sources:
            if source.get('type', None) == 'archive':
                url = source.get("url", None)

                if not url:
                    continue

                filename = url.rpartition('/')[2]
                match = re.search(r'(\d+\.\d+(?:\.\d+)?)', filename)

                if match:
                    return match.groups()[-1]
                else:
                    raise ValueError('Version not found in {}'.format(sources))
