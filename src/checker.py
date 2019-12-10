# Copyright © 2018–2019 Endless Mobile, Inc.
#
# Authors:
#       Joaquim Rocha <jrocha@endlessm.com>
#       Will Thompson <wjt@endlessm.com>
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
from .checkers import ALL_CHECKERS
from .lib.appdata import add_release_to_file
from .lib.externaldata import (
    ModuleData, ExternalData, ExternalDataSource,
)
from .lib.utils import read_manifest, dump_manifest

import logging
import os

import gi
from gi.repository import GLib

log = logging.getLogger(__name__)


class ManifestChecker:
    def __init__(self, manifest):
        self._manifest = manifest
        self._modules_data = {}
        self._external_data = {}

        # Initialize checkers
        self._checkers = [checker() for checker in ALL_CHECKERS]
        assert self._checkers

        # Map from filename to parsed contents of that file. Sources may be
        # specified as references to external files, which is why there can be
        # more than one file even though the input is a single filename.
        self._manifest_contents = {}

        # Top-level manifest contents
        data = self._read_manifest(self._manifest)
        # Map from manifest path to [ExternalData]
        self._collect_external_data(self._manifest, data)

    def _read_manifest(self, manifest_path):
        contents = read_manifest(manifest_path)
        self._manifest_contents[manifest_path] = contents
        return contents

    def _dump_manifest(self, path):
        """Writes back the cached contents of 'path', which may have been
        modified."""
        contents = self._manifest_contents[path]
        dump_manifest(contents, path)

    def _collect_external_data(self, path, json_data):
        for module in json_data.get('modules', []):
            if isinstance(module, str):
                module_path = os.path.join(os.path.dirname(path),
                                           module)
                log.debug("Loading modules from %s", module_path)

                try:
                    module = self._read_manifest(module_path)
                except GLib.Error as err:
                    if err.matches(GLib.quark_from_string('g-file-error-quark'), 4):
                        log.info("Referenced file not found: %s", module)
                        continue

                    raise
                except FileNotFoundError:
                    log.info("Referenced file not found: %s", module)
                    continue
            else:
                module_path = path

            module_name = module.get('name')
            module_data = ModuleData(module_name, module_path, module)

            sources = module.get('sources', [])
            external_sources = [ source for source in sources if isinstance(source, str) ]
            for source in external_sources:
                source_path = os.path.join(os.path.dirname(path), source)
                source_stat = os.stat(source_path)
                if source_stat.st_size > 102400:
                    log.info("External source file is over 100KB, skipping: %s", source)
                    external_sources.remove(source)

            external_data = self._external_data.setdefault(module_path, [])
            datas = ExternalDataSource.from_sources(module_path, sources)
            external_data.extend(datas)
            module_data.external_data.extend(datas)

            for external_source in external_sources:
                external_source_path = os.path.join(os.path.dirname(path),
                                                    external_source)
                external_source_data = self._read_manifest(external_source_path)
                datas = ExternalDataSource.from_sources(external_source_path, external_source_data)
                self._external_data[external_source_path] = datas
                module_data.external_data.extend(datas)

            self._modules_data[module_name] = module_data

    def check(self, filter_type=None):
        '''Perform the check for all the external data in the manifest

        It initializes an internal list of all the external data objects
        found in the manifest.
        '''
        ext_data_checked = []

        for _, module_data in self._modules_data.items():
            if not filter_type:
                external_data_filtered = module_data.external_data
            else:
                external_data_filtered = [ data for data in module_data.external_data if filter_type == data.type ]

            log.debug("Checking module %s (path: %s)", module_data.name, module_data.path)

            added = []
            for checker in self._checkers:
                module_added = checker.check_module(module_data, external_data_filtered)
                if module_added:
                    added.extend(module_added)

            ext_data_checked.extend(external_data_filtered)

            if added:
                ext_data_checked.extend(added)

                self._modules_data[module_data.name].external_data.extend(added)
                for data in added:
                    self._external_data[data.source_path].append(data)

        for path, external_data in self._external_data.items():
            if not filter_type:
                external_data_filtered = external_data
            else:
                external_data_filtered = [ data for data in external_data if filter_type == data.type ]

            log.debug("Checking individual sources in %s", path)

            n = len(external_data)
            for i, data in enumerate(external_data_filtered, 1):
                if data.state != ExternalData.State.UNKNOWN:
                    continue

                log.debug('[%d/%d] checking %s', i, n, data.filename)

                for checker in self._checkers:
                    checker.check(data)
                    if data.state != ExternalData.State.UNKNOWN:
                        break
                ext_data_checked.append(data)

        return list(set(ext_data_checked))

    def get_external_data(self, only_type=None):
        '''Returns the list of the external data found in the manifest

        Should be called after the 'check' method.
        'only_type' can be given for filtering the data of that type.
        '''
        return [
            data
            for datas in self._external_data.values()
            for data in datas
            if only_type is None or data.type == only_type
        ]

    def get_outdated_external_data(self):
        '''Returns a list of the outdated external data

        Outdated external data are the ones that either are broken
        (unreachable URL) or have a new version.
        '''
        return [
            data
            for data in self.get_external_data()
            if data.state == ExternalData.State.BROKEN or \
               data.state == ExternalData.State.ADDED or \
               data.state == ExternalData.State.REMOVED or \
               data.new_version
        ]

    def _update_manifest(self, path, datas, changes):
        last_update = None

        for data in datas:
            if data.new_version is None and \
               data.state != ExternalData.State.ADDED and \
               data.state != ExternalData.State.REMOVED:
                continue

            data.update()
            if data.state == ExternalData.State.ADDED:
                message = "Added {}".format(data.filename)
            elif data.state == ExternalData.State.REMOVED:
                message = "Removed {}".format(data.filename)
            elif data.new_version.version is not None:
                message = "Update {} to {}".format(
                    data.filename, data.new_version.version
                )
                last_update = data.new_version
            else:
                message = "Update {}".format(data.filename)

            changes[message] = None

        if changes:
            log.info("Updating %s", path)
            self._dump_manifest(path)

            appdata = os.path.splitext(self._manifest)[0] + ".appdata.xml"
            if last_update is not None and os.path.exists(appdata):
                # TODO: this assumes that the last changed source for which we can
                # detect a version number is the one corresponding to the application
                # as a whole. In practice, this is currently true, but in general it
                # may not be.
                add_release_to_file(appdata, last_update.version, last_update.timestamp.strftime("%F"))

    def update_manifests(self):
        """Updates references to external data in manifests."""
        # We want a list, without duplicates; Python provides an
        # insertion-order-preserving dictionary so we use that.
        changes = OrderedDict()
        for path, datas in self._external_data.items():
            self._update_manifest(path, datas, changes)

        return list(changes)
