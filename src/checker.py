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
import datetime
import typing as t
import asyncio
from dataclasses import dataclass

from .checkers import ALL_CHECKERS
from .lib.appdata import add_release_to_file
from .lib.externaldata import (
    ExternalData,
    ExternalDataSource,
    ExternalGitRepo,
    ExternalFile,
    ExternalGitRef,
    Checker,
)
from .lib.utils import read_manifest, dump_manifest

import logging
import os
from xml.sax import SAXParseException

from gi.repository import GLib


MAIN_SRC_PROP = "is-main-source"
MAX_MANIFEST_SIZE = 1024 * 100


log = logging.getLogger(__name__)


def find_appdata_file(directory, appid):
    for ext in ["appdata", "metainfo"]:
        appdata = os.path.join(directory, appid + "." + ext + ".xml")

        if not os.path.isfile(appdata):
            continue

        return appdata

    return None


def _external_source_filter(manifest_path: str, source) -> t.Optional[bool]:
    if not isinstance(source, str):
        return None
    source_path = os.path.join(os.path.dirname(manifest_path), source)
    source_size = os.stat(source_path).st_size
    if source_size > MAX_MANIFEST_SIZE:
        log.info(
            "External source file size %i KiB is over %i KiB, skipping: %s",
            source_size / 1024,
            MAX_MANIFEST_SIZE / 1024,
            source,
        )
        return False
    return True


class ManifestChecker:
    @dataclass
    class TasksCounter:
        started: int = 0
        finished: int = 0
        total: int = 0

    def __init__(self, manifest: str):
        self._root_manifest_path = manifest
        self._root_manifest_dir = os.path.dirname(self._root_manifest_path)
        self._external_data: t.Dict[str, t.List[t.Union[ExternalData, ExternalGitRepo]]]
        self._external_data = {}

        # Initialize checkers
        self._checkers: t.List[t.Type[Checker]]
        self._checkers = [checker_cls for checker_cls in ALL_CHECKERS]
        assert self._checkers

        # Map from filename to parsed contents of that file. Sources may be
        # specified as references to external files, which is why there can be
        # more than one file even though the input is a single filename.
        self._manifest_contents: t.Dict[str, t.Union[t.List, t.Dict]]
        self._manifest_contents = {}

        # Top-level manifest contents
        self._root_manifest = self._read_manifest(self._root_manifest_path)
        assert isinstance(self._root_manifest, dict)
        # Map from manifest path to [ExternalData]
        self._collect_external_data(self._root_manifest_path, self._root_manifest)

    def _read_manifest(self, manifest_path: str) -> t.Union[t.List, t.Dict]:
        contents = read_manifest(manifest_path)
        self._manifest_contents[manifest_path] = contents
        return contents

    def _dump_manifest(self, path):
        """Writes back the cached contents of 'path', which may have been
        modified."""
        contents = self._manifest_contents[path]
        dump_manifest(contents, path)

    def _collect_external_data(self, path, json_data):
        modules = json_data.get("modules")
        if modules is None:
            return
        elif not isinstance(modules, list):
            log.error('"modules" in %s is not a list', path)
            return
        for module in modules:
            if isinstance(module, str):
                module_path = os.path.join(os.path.dirname(path), module)
                log.info(
                    "Loading modules from %s",
                    os.path.relpath(module_path, self._root_manifest_dir),
                )

                try:
                    module = self._read_manifest(module_path)
                except GLib.Error as err:
                    if err.matches(GLib.quark_from_string("g-file-error-quark"), 4):
                        log.warning("Referenced file not found: %s", module)
                        continue

                    raise
                except FileNotFoundError:
                    log.warning("Referenced file not found: %s", module)
                    continue
            else:
                module_path = path

            self._collect_external_data(path=module_path, json_data=module)

            sources = module.get("sources", [])
            external_sources = [s for s in sources if _external_source_filter(path, s)]

            external_data = self._external_data.setdefault(module_path, [])
            datas = ExternalDataSource.from_sources(module_path, sources)
            external_data.extend(datas)

            for external_source in external_sources:
                external_source_path = os.path.join(
                    os.path.dirname(path), external_source
                )
                log.info(
                    "Loading sources from %s",
                    os.path.relpath(external_source_path, self._root_manifest_dir),
                )
                external_manifest = self._read_manifest(external_source_path)
                if isinstance(external_manifest, list):
                    external_source_data = external_manifest
                elif isinstance(external_manifest, dict):
                    external_source_data = [external_manifest]
                else:
                    raise TypeError(f"Invalid data type in {external_source_path}")
                datas = ExternalDataSource.from_sources(
                    external_source_path, external_source_data
                )
                self._external_data[external_source_path] = datas

    async def _check_data(
        self, counter: TasksCounter, data: t.Union[ExternalData, ExternalGitRepo]
    ):
        src_rel_path = os.path.relpath(data.source_path, self._root_manifest_dir)
        counter.started += 1
        log.info(
            "Started check [%d/%d] %s (from %s)",
            counter.started,
            counter.total,
            data.filename,
            src_rel_path,
        )
        for checker_cls in self._checkers:
            checker = checker_cls()
            if not checker.should_check(data):
                continue
            log.debug("Source %s: applying %s", data.filename, checker_cls.__name__)
            async with checker:
                await checker.check(data)
            if data.state != ExternalData.State.UNKNOWN:
                log.debug(
                    "Source %s: got new state %s from %s, skipping remaining checkers",
                    data.filename,
                    data.state.name,
                    checker_cls.__name__,
                )
                break
            if data.new_version is not None:
                log.debug(
                    "Source %s: got new version from %s, skipping remaining checkers",
                    data.filename,
                    checker_cls.__name__,
                )
                break
        counter.finished += 1
        log.info(
            "Finished check [%d/%d] %s (from %s)",
            counter.finished,
            counter.total,
            data.filename,
            src_rel_path,
        )
        return data

    async def check(self, filter_type=None):
        """Perform the check for all the external data in the manifest

        It initializes an internal list of all the external data objects
        found in the manifest.
        """
        external_data = sum(self._external_data.values(), [])
        if filter_type is not None:
            external_data = [d for d in external_data if d.type == filter_type]

        counter = self.TasksCounter(total=len(external_data))
        check_tasks = []
        for data in external_data:
            if data.state != ExternalData.State.UNKNOWN:
                continue
            check_tasks.append(self._check_data(counter, data))

        log.info("Checking %s external data items", counter.total)
        ext_data_checked = await asyncio.gather(*check_tasks)

        return list(set(ext_data_checked))

    def get_external_data(self, only_type=None):
        """Returns the list of the external data found in the manifest

        Should be called after the 'check' method.
        'only_type' can be given for filtering the data of that type.
        """
        return [
            data
            for datas in self._external_data.values()
            for data in datas
            if only_type is None or data.type == only_type
        ]

    def get_outdated_external_data(self):
        """Returns a list of the outdated external data

        Outdated external data are the ones that either are broken
        (unreachable URL) or have a new version.
        """
        return [
            data
            for data in self.get_external_data()
            if data.state == ExternalData.State.BROKEN or data.new_version
        ]

    def _update_manifest(self, path, datas, changes):
        for data in datas:
            if data.new_version is None:
                continue

            data.update()
            if data.new_version.version is not None:
                message = "Update {} to {}".format(
                    data.filename, data.new_version.version
                )
            else:
                message = "Update {}".format(data.filename)

            changes[message] = None

        if changes:
            log.info("Updating %s", path)
            self._dump_manifest(path)

    def _update_appdata(self):
        if "id" in self._root_manifest:
            app_id = self._root_manifest["id"]
        else:
            app_id = self._root_manifest["app-id"]

        appdata = find_appdata_file(os.path.dirname(self._root_manifest_path), app_id)
        if appdata is None:
            log.debug("Appdata not found for %s", app_id)
            return
        log.info("Preparing to update appdata %s", appdata)

        selected_data = None
        for data in self.get_external_data():
            if data.checker_data.get(MAIN_SRC_PROP):
                selected_data = data
                log.info("Selected upstream source: %s", selected_data.filename)
                break
            elif data.source_path == self._root_manifest_path:
                selected_data = data
        else:
            # Guess that the last external source in the root manifest is the one
            # corresponding to the main application bundle.
            assert selected_data is not None
            log.warning("Guessed upstream source: %s", selected_data.filename)

        last_update = selected_data.new_version

        version_changed = (
            last_update is not None
            and last_update.version is not None
            and (
                (
                    isinstance(last_update, ExternalFile)
                    and (
                        last_update.url != selected_data.current_version.url
                        # TODO We can't reliably tell if the appimage version stayed the same
                        # without downloading it, so just assume it changed
                        or last_update.url.endswith(".AppImage")
                    )
                )
                or (
                    isinstance(last_update, ExternalGitRef)
                    and last_update.tag != selected_data.current_version.tag
                )
            )
        )

        if version_changed:
            log.info("Version changed, adding release to %s", appdata)
            if last_update.timestamp is None:
                log.warning("Using current time in appdata release")
                timestamp = datetime.datetime.now()
            else:
                timestamp = last_update.timestamp
            try:
                add_release_to_file(
                    appdata, last_update.version, timestamp.strftime("%F")
                )
            except SAXParseException as err:
                log.error(str(err))
        else:
            log.debug("Version didn't change, not adding release")

    def update_manifests(self):
        """Updates references to external data in manifests."""
        # We want a list, without duplicates; Python provides an
        # insertion-order-preserving dictionary so we use that.
        changes = OrderedDict()
        for path, datas in self._external_data.items():
            self._update_manifest(path, datas, changes)
        if changes:
            self._update_appdata()

        return list(changes)
