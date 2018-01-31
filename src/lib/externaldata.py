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

from enum import Enum

import os
import pkgutil

class ExternalData:

    Type = Enum('Type', 'EXTRA_DATA FILE ARCHIVE')

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
