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
import dataclasses
import typing as t
import asyncio
from enum import IntEnum
import logging
import os

import aiohttp
from lxml.etree import XMLSyntaxError

from .lib import HTTP_CLIENT_HEADERS, TIMEOUT_CONNECT, TIMEOUT_TOTAL
from .lib.appdata import add_release_to_file
from .lib.externaldata import (
    BuilderModule,
    ExternalBase,
)
from .lib.utils import read_manifest, dump_manifest
from .lib.errors import (
    CheckerError,
    AppdataError,
    AppdataNotFound,
    AppdataLoadError,
    ManifestLoadError,
    ManifestFileTooLarge,
    ManifestFileOpenError,
    SourceLoadError,
    SourceUnsupported,
)
from .checkers import Checker, ALL_CHECKERS


MAIN_SRC_PROP = "is-main-source"
IMPORTANT_SRC_PROP = "is-important"
MAX_MANIFEST_SIZE = 1024 * 100


log = logging.getLogger(__name__)


def find_appdata_file(directory, appid):
    for ext in ["appdata", "metainfo"]:
        appdata = os.path.join(directory, appid + "." + ext + ".xml")

        if not os.path.isfile(appdata):
            continue

        return appdata

    return None


@dataclasses.dataclass(frozen=True)
class CheckerOptions:
    allow_unsafe: bool = False
    max_manifest_size: int = MAX_MANIFEST_SIZE
    require_important_update: bool = False


class ManifestChecker:
    class Kind(IntEnum):
        UNKNOWN = 0
        APP = 1
        MODULE = 2
        SOURCE = 4
        SOURCES = 8

    @dataclasses.dataclass
    class TasksCounter:
        started: int = 0
        finished: int = 0
        failed: int = 0
        total: int = 0

    def __init__(self, manifest: str, options: t.Optional[CheckerOptions] = None):
        self.kind = self.Kind.UNKNOWN
        self.app_id: t.Optional[str]
        self.app_id = None

        self.opts = options or CheckerOptions()

        self._root_manifest_path = manifest
        self._root_manifest_dir = os.path.dirname(self._root_manifest_path)

        self._modules: t.Dict[str, t.List[BuilderModule]] = {}
        self._external_data: t.Dict[str, t.List[ExternalBase]]
        self._external_data = {}

        self._errors: t.List[Exception]
        self._errors = []

        # Initialize checkers
        self._checkers: t.List[t.Type[Checker]]
        self._checkers = [checker_cls for checker_cls in ALL_CHECKERS]
        assert self._checkers

        # Map from filename to parsed contents of that file. Sources may be
        # specified as references to external files, which is why there can be
        # more than one file even though the input is a single filename.
        self._manifest_contents: t.Dict[str, t.Union[t.List, t.Dict]]
        self._manifest_contents = {}

        self._manifest_headers: t.Dict[str, bool] = {}

        # Top-level manifest contents
        self._root_manifest = self._read_manifest(self._root_manifest_path)
        self._load_root_manifest()

        # Map from manifest path to [ExternalBase]
        self._collect_external_data()

    def _load_root_manifest(self):
        if isinstance(self._root_manifest, list):
            self.kind = self.Kind.SOURCES
            return
        assert isinstance(self._root_manifest, dict)
        if "id" in self._root_manifest or "app-id" in self._root_manifest:
            self.kind = self.Kind.APP
            self.app_id = self._root_manifest.get(
                "id", self._root_manifest.get("app-id")
            )
            return
        if "name" in self._root_manifest and (
            "sources" in self._root_manifest or "modules" in self._root_manifest
        ):
            self.kind = self.Kind.MODULE
            return
        if "type" in self._root_manifest:
            self.kind = self.Kind.SOURCE
            return
        raise ManifestLoadError("Can't determine manifest kind")

    def _read_manifest(self, manifest_path: str) -> t.Union[t.List, t.Dict]:
        if manifest_path in self._manifest_contents:
            return self._manifest_contents[manifest_path]
        try:
            manifest_size = os.stat(manifest_path).st_size
            if manifest_size > self.opts.max_manifest_size:
                raise ManifestFileTooLarge(
                    f"Manifest file size {manifest_size / 1024:.1f} KiB exceeds "
                    f"{self.opts.max_manifest_size / 1024:.1f} KiB: {manifest_path}"
                )
            ret = read_manifest(manifest_path)
            if isinstance(ret, tuple):
                contents, has_yaml_header = ret
            else:
                contents, has_yaml_header = ret, False
        except FileNotFoundError as err:
            raise ManifestFileOpenError from err
        self._manifest_contents[manifest_path] = contents
        self._manifest_headers[manifest_path] = has_yaml_header
        return contents

    def _dump_manifest(self, path):
        """Writes back the cached contents of 'path', which may have been
        modified."""
        contents = self._manifest_contents[path]
        has_yaml_header = self._manifest_headers.get(path, False)
        dump_manifest(contents, path, has_yaml_header)

    def _collect_external_data(self):
        if self.kind == self.Kind.APP:
            modules = self._root_manifest.get("modules", [])
            assert isinstance(modules, list)
            for module in modules:
                self._collect_module_data(self._root_manifest_path, module)
        elif self.kind == self.Kind.MODULE:
            self._collect_module_data(self._root_manifest_path, self._root_manifest)
        elif self.kind in [self.Kind.SOURCE, self.Kind.SOURCES]:
            self._collect_source_data(self._root_manifest_path, self._root_manifest)

        # Establish parent-child relation between sources
        # FIXME Expensive O(n^2) algorithm; perhaps we can do better?
        collected_data = self.get_external_data()
        for data in collected_data:
            if "parent-id" not in data.checker_data:
                continue
            # Assign parent source object
            assert data.parent is None
            parent_id = data.checker_data["parent-id"]
            try:
                data.parent = next(d for d in collected_data if d.ident == parent_id)
            except StopIteration as err:
                raise ManifestLoadError(
                    f'Source {data}: parent source with ID "{parent_id}" not found'
                ) from err
            # Check for inheritance loops
            parent = data.parent
            while parent is not None:
                if parent is data:
                    raise ManifestLoadError(f"Source {data}: inheritance loop detected")
                parent = parent.parent

    def _collect_module_data(
        self,
        module_path: str,
        module: t.Union[str, t.Dict],
        parent: t.Optional[BuilderModule] = None,
    ):
        if isinstance(module, str):
            ext_module_path = os.path.join(os.path.dirname(module_path), module)
            log.info(
                "Loading module from %s",
                os.path.relpath(ext_module_path, self._root_manifest_dir),
            )

            try:
                ext_module = self._read_manifest(ext_module_path)
            except ManifestFileOpenError as err:
                log.warning(err)
                return

            assert isinstance(ext_module, dict), ext_module_path
            return self._collect_module_data(ext_module_path, ext_module, parent=parent)

        module_obj = BuilderModule.from_manifest(module_path, module, parent)
        self._modules.setdefault(module_path, []).append(module_obj)

        child_modules = module.get("modules", [])
        if not isinstance(child_modules, list):
            log.error('"modules" in %s is not a list', module_path)
            child_modules = []
        for child_module in child_modules:
            self._collect_module_data(module_path, child_module, parent=module_obj)

        self._collect_source_data(module_path, module.get("sources", []), module_obj)

    def _collect_source_data(
        self,
        source_path: str,
        source: t.Union[str, t.Dict, t.List[t.Union[str, t.Dict]]],
        module: t.Optional[BuilderModule] = None,
        is_external: bool = False,  # This source mf was referenced from another mf
    ):
        if isinstance(source, list):
            for child_source in source:
                assert isinstance(child_source, (str, dict))
                self._collect_source_data(
                    source_path, child_source, module, is_external
                )
            return

        if isinstance(source, str):
            if is_external:
                raise ManifestLoadError(
                    "Nested external source manifests not allowed: "
                    f"{source} referenced from {source_path}"
                )
            ext_source_path = os.path.join(os.path.dirname(source_path), source)
            log.info(
                "Loading sources from %s",
                os.path.relpath(ext_source_path, self._root_manifest_dir),
            )
            try:
                ext_source = self._read_manifest(ext_source_path)
            except ManifestFileTooLarge as err:
                log.warning(err)
            else:
                self._collect_source_data(
                    ext_source_path, ext_source, module, is_external=True
                )
            return

        assert isinstance(source, dict)
        # Collect only sources we didn't collect previously
        # NOTE here we rely on ruamel.yaml to make YAML aliases into
        # pointers to the same dict object ridden from YAML anchor
        manifest_datas = self._external_data.setdefault(source_path, [])
        try:
            data = next(d for d in manifest_datas if d.source is source)
        except StopIteration:
            # Didn't find this source previously loaded, proceed to loading it
            pass
        else:
            # Found this source previously loaded, add it to the module
            log.debug("Source %s already loaded, first seen from %s", data, data.module)
            if module:
                module.sources.append(data)
            return

        try:
            data = ExternalBase.from_source(source_path, source, module)
        except SourceUnsupported as err:
            log.debug(err)
        except SourceLoadError as err:
            self._errors.append(err)
            log.error(err)
        else:
            manifest_datas.append(data)
            if module:
                module.sources.append(data)

    async def _check_data(
        self,
        counter: TasksCounter,
        http_session: aiohttp.ClientSession,
        data: ExternalBase,
    ) -> ExternalBase:
        src_rel_path = os.path.relpath(data.source_path, self._root_manifest_dir)
        if data.parent:
            await data.parent.checked.wait()
        data.checked.clear()
        counter.started += 1
        checkers = [c(http_session) for c in self._checkers if c.should_check(data)]
        if not checkers:
            counter.finished += 1
            log.info(
                "Skipped check [%d/%d] %s (from %s)",
                counter.started,
                counter.total,
                data,
                src_rel_path,
            )
            return data
        log.info(
            "Started check [%d/%d] %s (from %s)",
            counter.started,
            counter.total,
            data,
            src_rel_path,
        )
        for checker in checkers:
            log.debug(
                "Source %s: applying %s",
                data,
                checker.__class__.__name__,
            )
            try:
                await checker.validate_checker_data(data)
                await checker.check(data)
            except CheckerError as err:
                self._errors.append(err)
                counter.failed += 1
                log.error(
                    "Failed to check %s with %s: %s",
                    data,
                    checker.__class__.__name__,
                    err,
                )
                # TODO: Potentially we can proceed to the next applicable checker here,
                # but applying checkers in sequence should be carefully tested.
                # This is a safety switch: leave the data alone on error.
                data.checked.set()
                return data
            if data.state != data.State.UNKNOWN:
                log.debug(
                    "Source %s: got new %s from %s, skipping remaining checkers",
                    data,
                    data.state,
                    checker.__class__.__name__,
                )
                break
            if data.new_version is not None:
                log.debug(
                    "Source %s: got new version from %s, skipping remaining checkers",
                    data,
                    checker.__class__.__name__,
                )
                break
        counter.finished += 1
        log.info(
            "Finished check [%d/%d] %s (from %s)",
            counter.finished + counter.failed,
            counter.total,
            data,
            src_rel_path,
        )
        data.checked.set()
        return data

    async def check(self, filter_type=None) -> t.List[ExternalBase]:
        """Perform the check for all the external data in the manifest

        It initializes an internal list of all the external data objects
        found in the manifest.
        """
        external_data: t.List[ExternalBase]
        external_data = sum(self._external_data.values(), [])
        if filter_type is not None:
            external_data = [d for d in external_data if d.type == filter_type]

        counter = self.TasksCounter(total=len(external_data))
        async with aiohttp.ClientSession(
            raise_for_status=True,
            headers=HTTP_CLIENT_HEADERS,
            timeout=aiohttp.ClientTimeout(connect=TIMEOUT_CONNECT, total=TIMEOUT_TOTAL),
        ) as http_session:
            check_tasks = []
            for data in external_data:
                if data.state != data.State.UNKNOWN:
                    continue
                check_tasks.append(self._check_data(counter, http_session, data))

            log.info("Checking %s external data items", counter.total)
            ext_data_checked = await asyncio.gather(*check_tasks)

        return ext_data_checked

    def get_external_data(self, only_type=None) -> t.List[ExternalBase]:
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

    def get_errors(
        self,
        only_type: t.Optional[t.Type[Exception]] = None,
    ) -> t.List[Exception]:
        """Return a list of errors occurred while checking/updating the manifest"""

        return [
            e for e in self._errors if only_type is None or isinstance(e, only_type)
        ]

    def get_outdated_external_data(self) -> t.List[ExternalBase]:
        """Returns a list of the outdated external data

        Outdated external data are the ones that either are broken
        (unreachable URL) or have a new version.
        """
        return [
            data
            for data in self.get_external_data()
            if data.State.BROKEN in data.state or data.new_version
        ]

    def _update_manifest(self, path, datas, changes):
        path_has_changes = False
        for data in datas:
            if data.new_version is None:
                continue

            data.update()
            if data.new_version.version is not None:
                message = "{}: Update {} to {}".format(
                    data.module, data.filename, data.new_version.version
                )
            else:
                message = "{}: Update {}".format(data.module, data.filename)

            changes[message] = None
            path_has_changes = True

        if path_has_changes:
            log.info("Updating %s", path)
            self._dump_manifest(path)

    def _update_appdata(self):
        if not self.app_id:
            raise AppdataNotFound(f"No app ID in {self._root_manifest_path}")

        appdata = find_appdata_file(
            os.path.dirname(self._root_manifest_path), self.app_id
        )
        if appdata is None:
            raise AppdataNotFound(f"Can't find appdata file matching {self.app_id}")

        log.info("Preparing to update appdata %s", appdata)

        selected_data = None
        for data in self.get_external_data():
            if data.checker_data.get(MAIN_SRC_PROP):
                selected_data = data
                log.info("Selected upstream source: %s", selected_data)
                break
            elif data.source_path == self._root_manifest_path:
                selected_data = data
        else:
            if selected_data is not None:
                # Guess that the last external source in the root manifest is the one
                # corresponding to the main application bundle.
                log.warning("Guessed last source as main source: %s", selected_data)
            else:
                log.error(
                    (
                        "No main source configured and no external source in "
                        "%s. Not updating appdata"
                    ),
                    self._root_manifest_path,
                )
                return

        last_update = selected_data.new_version

        if selected_data.has_version_changed:
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
            except XMLSyntaxError as err:
                raise AppdataLoadError from err
        else:
            log.debug("Version didn't change, not adding release")

    def update_manifests(self) -> t.List[str]:
        """Updates references to external data in manifests.

        If require_important_update is True, only update the manifest if at least one
        source with IMPORTANT_SRC_PROP or MAIN_SRC_PROP received an update.
        """
        # We want a list, without duplicates; Python provides an
        # insertion-order-preserving dictionary so we use that.
        changes: t.Dict[str, t.Any]
        changes = OrderedDict()

        found_important_update = None

        if self.opts.require_important_update:
            for data in self.get_external_data():
                important = data.checker_data.get(IMPORTANT_SRC_PROP)
                main = data.checker_data.get(MAIN_SRC_PROP)
                if important or (main and important is not False):
                    log.debug("Found an important source: %s", data)

                    found_important_update = data.has_version_changed

                    if found_important_update:
                        log.info(
                            "Update found for important source: %s, updating manifest",
                            data,
                        )
                        break

        if found_important_update or found_important_update is None:
            for path, datas in self._external_data.items():
                self._update_manifest(path, datas, changes)
            if changes:
                try:
                    self._update_appdata()
                except AppdataNotFound as err:
                    log.info("Not updating appdata: %s", err)
                except AppdataError as err:
                    self._errors.append(err)
                    log.error(err)

        else:
            log.info("No important source had an update, not updating manifest")

        return list(changes)
