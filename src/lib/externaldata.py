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

from collections import OrderedDict
from enum import Enum

import json
import os
import pkgutil

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
        self.current_version = None
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

class Checker:

    def check(self, external_data):
        raise NotImplementedError()

class CheckerRegistry:

    _checkers = []

    @staticmethod
    def load(checkers_folder):
        for _unused, modname, _unused in pkgutil.walk_packages([checkers_folder]):
            pkg_name = os.path.basename(checkers_folder)
            __import__(pkg_name + '.' + modname)

    @classmethod
    def register_checker(class_, checker):
        if not issubclass(checker, Checker):
            raise TypeError('{} is not a of type {}'.format(checker, Checker))
        class_._checkers.append(checker)

    @classmethod
    def get_checkers(class_):
        return class_._checkers
