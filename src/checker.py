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
from lib.externaldata import CheckerRegistry, ExternalData

import json
import os
import re

class NoManifestCheckersFound(Exception):
    pass

class ManifestChecker:

    def __init__(self, manifest):
        self._manifest = manifest
        self._external_data = []

        # Load and initialize checkers
        CheckerRegistry.load(os.path.join(os.path.dirname(__file__), 'checkers'))
        self._checkers = [checker() for checker in CheckerRegistry.get_checkers()]

        with open(self._manifest, 'r') as manifest_file:
            # Strip manifest of c-style comments (happens in some Flatpak manifests)
            clean_manifest = re.sub(r'(^|\s)/\*.*?\*/', '', manifest_file.read())
            self._json_data = json.loads(clean_manifest, object_pairs_hook=OrderedDict)

        self._collect_external_data()

    def _collect_external_data(self):
        self._external_data = self._get_module_data_from_json(self._json_data) + \
                              self._get_finish_args_extra_data_from_json(self._json_data)

    def _get_finish_args_extra_data_from_json(self, json_data):
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

    def _get_module_data_from_json(self, json_data):
        external_data = []
        for module in json_data.get('modules', []):
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
                data_type = self._translate_data_type(data_type)
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

    def _translate_data_type(self, data_type):
        types = { 'file': ExternalData.Type.FILE,
                  'archive': ExternalData.Type.ARCHIVE,
                  'extra-data': ExternalData.Type.EXTRA_DATA}
        return types.get(data_type)

    def print_external_data(self):
        for data in self._external_data:
            print(data)

    def check(self, filter_type=None):
        '''Perform the check for all the external data in the manifest

        It initializes an internal list of all the external data objects
        found in the manifest.
        '''

        if not self._checkers:
            raise NoManifestCheckersFound()

        ext_data_checked = []
        for data in self._external_data:
            # Ignore if the type is not the one we care about
            if filter_type is not None and filter_type != data.type:
                continue;

            for checker in self._checkers:
                checker.check(data)
            ext_data_checked.append(data)

        return ext_data_checked

    def get_external_data(self, only_type=None):
        '''Returns the list of the external data found in the manifest

        Should be called after the 'check' method.
        'only_type' can be given for filtering the data of that type.
        '''
        if only_type is None:
            return list(self._external_data)
        return [data for data in self._external_data if data.type == only_type]

    def get_outdated_external_data(self):
        '''Returns a list of the outdated external data

        Outdated external data are the ones that either are broken
        (unreachable URL) or have a new version.
        '''
        external_data = []
        for data in self._external_data:
            if data.state == ExternalData.State.BROKEN or \
               data.new_version:
                external_data.append(data)

        return external_data
