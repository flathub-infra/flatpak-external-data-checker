# Copyright (C) 2018 Endless Mobile, Inc.
#
# Authors:
#       Andrew Hayzen <ahayzen@gmail.com>
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

from collections import OrderedDict
from enum import Enum

import json
import os

class ExternalData:

    Type = Enum('Type', 'EXTRA_DATA FILE ARCHIVE')

    _TYPES_MANIFEST_MAP = {Type.EXTRA_DATA: 'extra-data',
                           Type.FILE: 'file',
                           Type.ARCHIVE: 'archive'}
    _NAME_MANIFEST_MAP = {Type.EXTRA_DATA: 'filename',
                          Type.FILE: 'dest-filename',
                          Type.ARCHIVE: 'dest-filename'}

    class State(Enum):
        UNKNOWN = 0
        VALID = 1 << 1 # URL is reachable
        BROKEN = 1 << 2 # URL couldn't be reached

    def __init__(self, data_type, filename, url, checksum, size=-1, arches=[],
                 checker_data=None):
        self.filename = filename
        self.url = url
        self.checksum = checksum
        self.size = int(size)
        self.arches = arches
        self.type = data_type
        self.checker_data = checker_data
        self.new_version = None
        self.state = ExternalData.State.UNKNOWN

    def __str__(self):
        info = '{filename}:\n' \
               '  State:   {state}\n' \
               '  Type:    {type}\n' \
               '  URL:     {url}\n' \
               '  SHA256:  {checksum}\n' \
               '  Size:    {size}\n' \
               '  Arches:  {arches}\n' \
               '  Checker: {checker_data}'.format(state=self.state.name,
                                                  filename=self.filename,
                                                  type=self.type.name,
                                                  url=self.url,
                                                  checksum=self.checksum,
                                                  size=self.size,
                                                  arches=self.arches,
                                                  checker_data=self.checker_data)
        return info

    @staticmethod
    def collect_external_data(json_data):
        return ExternalData._get_module_data_from_json(json_data) + \
            ExternalData._get_finish_args_extra_data_from_json(json_data)

    @staticmethod
    def _get_finish_args_extra_data_from_json(json_data):
        extra_data_prefix = '--extra-data='
        external_data = []
        extra_data_str = [arg for arg in json_data.get('finish-args', []) \
                          if arg.startswith(extra_data_prefix)]

        for extra_data in extra_data_str:
            # discard '--extra-data=' prefix from the string
            extra_data = extra_data[len(extra_data_prefix) + 1:]
            info, url = extra_data.split('::')
            name, sha256sum, size = info.split(':')
            data_type = ExternalData.Type.EXTRA_DATA
            ext_data = ExternalData(data_type, name, url, sha256sum, size, [])
            external_data.append(ext_data)

        return external_data

    @staticmethod
    def _get_module_data_from_json(json_data):
        external_data = []
        for module in json_data.get('modules', []):
            if type(module) is str:
                continue  # FIXME: already upstream

            for source in module.get('sources', []):
                url = source.get('url', None)
                if not url:
                    continue

                name = source.get('filename')
                if not name:
                    name = source.get('dest-filename')
                if not name:
                    name = os.path.basename(url)

                data_type = source.get('type')
                data_type = ExternalData._translate_data_type(data_type)
                if data_type is None:
                    continue

                sha256sum = source.get('sha256', None)
                arches = source.get('only-arches', [])
                size = source.get('size', -1)
                checker_data = source.get('x-checker-data')

                ext_data = ExternalData(data_type, name, url, sha256sum, size,
                                        arches, checker_data)
                external_data.append(ext_data)

        return external_data

    @staticmethod
    def _translate_data_type(data_type):
        # FIXME: reverse map of _TYPES_MANIFEST_MAP
        types = { 'file': ExternalData.Type.FILE,
                  'archive': ExternalData.Type.ARCHIVE,
                  'extra-data': ExternalData.Type.EXTRA_DATA}
        return types.get(data_type)

    def to_json(self):
        json_data = OrderedDict()
        json_data['type'] = __class__._TYPES_MANIFEST_MAP[self.type]
        json_data[__class__._TYPES_MANIFEST_MAP[self.type]] = self.filename
        json_data['url'] = self.url
        json_data['sha256'] = self.checksum

        if self.arches:
            json_data['only-arches'] = self.arches

        if self.size >= 0:
            json_data['size'] = self.size

        if self.checker_data:
            json_data['x-checker-data'] = self.checker_data

        return json.dumps(json_data, indent=4)
