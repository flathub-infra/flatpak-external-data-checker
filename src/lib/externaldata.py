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

import abc
from collections import OrderedDict, namedtuple
from enum import Enum

import json
import os
import pkgutil


ExternalFile = namedtuple('ExternalFile', ('url', 'checksum', 'size'))


class ExternalData(abc.ABC):
    Type = Enum('Type', 'EXTRA_DATA FILE ARCHIVE')

    TYPES = {
        'file': Type.FILE,
        'archive': Type.ARCHIVE,
        'extra-data': Type.EXTRA_DATA,
    }
    _TYPES_MANIFEST_MAP = {Type.EXTRA_DATA: 'extra-data',
                           Type.FILE: 'file',
                           Type.ARCHIVE: 'archive'}
    _NAME_MANIFEST_MAP = {Type.EXTRA_DATA: 'filename',
                          Type.FILE: 'dest-filename',
                          Type.ARCHIVE: 'dest-filename'}

    class State(Enum):
        UNKNOWN = 0
        VALID = 1 << 1  # URL is reachable
        BROKEN = 1 << 2  # URL couldn't be reached

    def __init__(self, data_type, filename, url, checksum, size=-1, arches=[],
                 checker_data=None):
        self.filename = filename
        self.arches = arches
        self.type = data_type
        self.checker_data = checker_data
        self.current_version = ExternalFile(url, checksum, int(size))
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
        json_data['type'] = self._TYPES_MANIFEST_MAP[self.type]
        json_data[self._TYPES_MANIFEST_MAP[self.type]] = self.filename

        external_file = self.new_version or self.current_version
        json_data['url'] = external_file.url
        json_data['sha256'] = external_file.checksum
        if external_file.size >= 0:
            json_data['size'] = external_file.size

        if self.arches:
            json_data['only-arches'] = self.arches

        if self.checker_data:
            json_data['x-checker-data'] = self.checker_data

        return json.dumps(json_data, indent=4)


class ExternalDataSource(ExternalData):
    def __init__(self, source, data_type, url):
        name = (
            source.get('filename') or
            source.get('dest-filename') or
            os.path.basename(url)
        )

        sha256sum = source.get('sha256', None)
        arches = source.get('only-arches', [])
        size = source.get('size', -1)
        checker_data = source.get('x-checker-data')
        super().__init__(
            data_type, name, url, sha256sum, size, arches, checker_data,
        )
        self.source = source

    @classmethod
    def from_sources(cls, sources):
        external_data = []

        for source in sources:
            url = source.get('url')
            data_type = cls.TYPES.get(source.get('type'))
            if url is None or data_type is None:
                continue

            external_data.append(cls(source, data_type, url))

        return external_data


class ExternalDataFinishArg(ExternalData):
    PREFIX = '--extra-data='

    def __init__(self, finish_args, index):
        arg = finish_args[index]
        # discard '--extra-data=' prefix from the string
        extra_data = arg[len(self.PREFIX) + 1:]
        info, url = extra_data.split('::')
        name, sha256sum, size = info.split(':')
        data_type = ExternalData.Type.EXTRA_DATA

        super().__init__(data_type, name, url, sha256sum, size, [])

        self.finish_args = finish_args
        self.index = index

    @classmethod
    def from_args(cls, finish_args):
        return [
            cls(finish_args, i)
            for i, arg in enumerate(finish_args)
            if arg.startswith(cls.PREFIX)
        ]


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
