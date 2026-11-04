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

import dataclasses
import datetime
import logging
import os
import typing as t
from collections import OrderedDict
from enum import IntEnum

import aiohttp
from lxml.etree import XMLSyntaxError

from .checkers import ALL_CHECKERS, Checker
from .lib import HTTP_CLIENT_HEADERS, TIMEOUT_CONNECT, TIMEOUT_SOCK_READ, TIMEOUT_TOTAL
from .lib.appdata import add_release_to_file, read_current_version
from .lib.errors import (
    AppdataError,
    AppdataLoadError,
    AppdataNotFound,
    CheckerError,
    ManifestFileOpenError,
    ManifestFileTooLarge,
    ManifestLoadError,
    SourceLoadError,
    SourceUnsupported,
    UpdateGroupError,
)
from .lib.externaldata import (
    BuilderModule,
    ExternalBase,
)
from .lib.robots import RobotsCache
from .lib.sourceupdates import SourceUpdate
from .lib.utils import asyncio_gather_failfast, dump_manifest, read_manifest

MAIN_SRC_PROP = "is-main-source"
IMPORTANT_SRC_PROP = "is-important"
RELEASE_URL_TEMPLATE_PROP = "release-url-template"
ROBOTS_TXT_PROP = "enable-robots-txt"
MAX_MANIFEST_SIZE = 1024 * 1024
# aiohttp's 8190 byte default is too small for some servers, e.g. GitHub's
# release download redirects carry a ~9 KiB Content-Security-Policy header.
MAXIMUM_HEADER_SIZE = 65536


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
    enable_robots_txt: bool = False


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

    def __init__(self, manifest: str, options: CheckerOptions | None = None):
        self.kind = self.Kind.UNKNOWN
        self.app_id: str | None
        self.app_id = None

        self.opts = options or CheckerOptions()

        self._root_manifest_path = manifest
        self._root_manifest_dir = os.path.dirname(self._root_manifest_path)

        self._modules: dict[str, list[BuilderModule]] = {}
        self._external_data: dict[str, list[ExternalBase]]
        self._external_data = {}

        self._errors: list[Exception]
        self._errors = []
        self.source_updates: list[SourceUpdate] = []
        self._allowed_updates: set[int] | None = None
        self._blocked_update_sources: set[int] = set()
        self._source_checks: dict[int, list[str]] = {}
        self._source_errors: dict[int, list[Exception]] = {}
        self._filter_type = None

        # Initialize checkers
        self._checkers: list[type[Checker]]
        self._checkers = [checker_cls for checker_cls in ALL_CHECKERS]
        assert self._checkers

        # Map from filename to parsed contents of that file. Sources may be
        # specified as references to external files, which is why there can be
        # more than one file even though the input is a single filename.
        self._manifest_contents: dict[str, list | dict]
        self._manifest_contents = {}

        self._manifest_headers: dict[str, bool] = {}
        self._incomplete_manifests: list[ManifestLoadError] = []

        # Top-level manifest contents
        self._root_manifest = self._read_manifest(self._root_manifest_path)
        self._load_root_manifest()

        # Map from manifest path to [ExternalBase]
        self._collect_external_data()
        self._update_groups: dict[str, list[ExternalBase]] = {}
        for data in self.get_external_data():
            group = data.checker_data.get("update-group")
            if group is not None:
                self._update_groups.setdefault(group, []).append(data)
        if self._update_groups and self._incomplete_manifests:
            raise ManifestLoadError(
                "Cannot validate update groups in an incomplete manifest"
            ) from self._incomplete_manifests[0]
        for group, members in self._update_groups.items():
            if len(members) < 2:
                raise ManifestLoadError(
                    f"Update group {group!r} must contain at least two sources"
                )
        self._discover_current_versions()

    def _main_sources(self) -> list[ExternalBase]:
        sources = self.get_external_data()
        selected = [data for data in sources if data.checker_data.get(MAIN_SRC_PROP)]
        if selected:
            return selected
        root_sources = [
            data for data in sources if data.source_path == self._root_manifest_path
        ]
        if root_sources:
            log.warning("Guessed last source as main source: %s", root_sources[-1])
            return root_sources[-1:]
        return []

    def _discover_current_versions(self):
        sources = self.get_external_data()
        for data in sources:
            data.discover_current_version()
        if not self.app_id or not sources:
            return
        appdata = find_appdata_file(self._root_manifest_dir, self.app_id)
        if appdata is None:
            return
        selected = [
            data for data in self._main_sources() if not data.current_version.version
        ]
        if not selected:
            return
        try:
            version = read_current_version(appdata, self.app_id)
        except (OSError, XMLSyntaxError) as err:
            log.warning("Cannot read current version from %s: %s", appdata, err)
            return
        if version:
            for data in selected:
                data.current_version = data.current_version._replace(version=version)
                log.info("Source %s: using metainfo version %s", data, version)

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

    def _read_manifest(self, manifest_path: str) -> list | dict:
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
            assert isinstance(contents, (list, dict))
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
        dump_manifest(t.cast(dict, contents), path, has_yaml_header)

    def _collect_external_data(self):
        if self.kind == self.Kind.APP:
            assert isinstance(self._root_manifest, dict)
            modules = self._root_manifest.get("modules", [])
            assert isinstance(modules, list)
            for module in modules:
                self._collect_module_data(self._root_manifest_path, module)
        elif self.kind == self.Kind.MODULE:
            assert isinstance(self._root_manifest, dict)
            self._collect_module_data(self._root_manifest_path, self._root_manifest)
        elif self.kind in [self.Kind.SOURCE, self.Kind.SOURCES]:
            self._collect_source_data(self._root_manifest_path, self._root_manifest)

        # Establish parent-child relation between sources
        collected_data = self.get_external_data()
        source_ids: set[str] = set()
        for data in collected_data:
            source_id = data.checker_data.get("source-id")
            if source_id is not None:
                if not source_id or source_id in source_ids:
                    raise ManifestLoadError(
                        f"Empty or duplicate source-id: {source_id!r}"
                    )
                source_ids.add(source_id)
        sources_by_id: dict[str, ExternalBase] = {}
        unindexed_sources = iter(collected_data)
        for data in collected_data:
            if "parent-id" not in data.checker_data:
                continue
            assert data.parent is None
            parent_id = data.checker_data["parent-id"]
            # Unused IDs may raise; index only through the first match.
            if parent_id not in sources_by_id:
                for candidate in unindexed_sources:
                    candidate_id = candidate.ident
                    sources_by_id.setdefault(candidate_id, candidate)
                    if candidate_id == parent_id:
                        break
            try:
                data.parent = sources_by_id[parent_id]
            except KeyError as err:
                raise ManifestLoadError(
                    f'Source {data}: parent source with ID "{parent_id}" not found'
                ) from err
            # Check each new link to preserve error order. Long chains remain O(n²).
            parent: ExternalBase | None = data.parent
            while parent is not None:
                if parent is data:
                    raise ManifestLoadError(f"Source {data}: inheritance loop detected")
                parent = parent.parent

    def _collect_module_data(
        self,
        module_path: str,
        module: str | dict,
        parent: BuilderModule | None = None,
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
                self._incomplete_manifests.append(err)
                log.warning(err)
                return None

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
        source: str | dict | list[str | dict],
        module: BuilderModule | None = None,
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
                self._incomplete_manifests.append(err)
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
            metadata = source.get("x-checker-data", {})
            if isinstance(metadata, dict) and "update-group" in metadata:
                raise ManifestLoadError(
                    f"Cannot load update-group member in {source_path}: {err}"
                ) from err
            log.debug(err)
        except SourceLoadError as err:
            metadata = source.get("x-checker-data", {})
            if isinstance(metadata, dict) and "update-group" in metadata:
                raise ManifestLoadError(
                    f"Cannot load update-group member in {source_path}: {err}"
                ) from err
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
        global_robots_cache: RobotsCache,
    ) -> ExternalBase:
        src_rel_path = os.path.relpath(data.source_path, self._root_manifest_dir)
        if data.parent:
            await data.parent.checked.wait()
        data.checked.clear()
        counter.started += 1
        enable_robots = self.opts.enable_robots_txt or data.checker_data.get(
            ROBOTS_TXT_PROP, False
        )
        robots_cache = global_robots_cache if enable_robots else None
        checkers = [
            c(http_session, robots_cache)
            for c in self._checkers
            if c.should_check(data)
        ]
        if not checkers:
            counter.finished += 1
            log.info(
                "Skipped check [%d/%d] %s (from %s)",
                counter.started,
                counter.total,
                data,
                src_rel_path,
            )
            data.checked.set()
            return data
        log.info(
            "Started check [%d/%d] %s (from %s)",
            counter.started,
            counter.total,
            data,
            src_rel_path,
        )
        for checker in checkers:
            self._source_checks.setdefault(id(data), []).append(
                checker.__class__.__name__
            )
            log.debug(
                "Source %s: applying %s",
                data,
                checker.__class__.__name__,
            )
            try:
                await checker.validate_checker_data(data)
                await checker.check(data)
            except CheckerError as err:
                data.check_failed = True
                self._source_errors.setdefault(id(data), []).append(err)
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

    async def check(self, filter_type=None) -> list[ExternalBase]:
        """Perform the check for all the external data in the manifest

        It initializes an internal list of all the external data objects
        found in the manifest.
        """
        self._filter_type = filter_type
        external_data: list[ExternalBase]
        external_data = sum(self._external_data.values(), [])
        # Prepare every dependency before starting tasks. Unscheduled sources
        # already have their final data for this run; children must not wait for
        # them. Clear scheduled sources here to avoid stale events on rechecks.
        for data in external_data:
            if data.state == data.State.UNKNOWN and (
                filter_type is None or data.type == filter_type
            ):
                data.checked.clear()
            else:
                data.checked.set()
        if filter_type is not None:
            external_data = [d for d in external_data if d.type == filter_type]

        counter = self.TasksCounter(total=len(external_data))
        async with aiohttp.ClientSession(
            raise_for_status=True,
            headers=HTTP_CLIENT_HEADERS,
            timeout=aiohttp.ClientTimeout(
                connect=TIMEOUT_CONNECT,
                sock_read=TIMEOUT_SOCK_READ,
                total=TIMEOUT_TOTAL,
            ),
            trust_env=True,
            max_field_size=MAXIMUM_HEADER_SIZE,
            max_line_size=MAXIMUM_HEADER_SIZE,
        ) as http_session:
            global_robots_cache = RobotsCache(http_session)
            check_tasks = []
            for data in external_data:
                if data.state != data.State.UNKNOWN:
                    continue
                check_tasks.append(
                    self._check_data(counter, http_session, data, global_robots_cache)
                )

            log.info("Checking %s external data items", counter.total)
            ext_data_checked = await asyncio_gather_failfast(check_tasks)

        self._validate_update_groups()
        return ext_data_checked

    def _validate_update_groups(self, allowed: list[ExternalBase] | None = None):
        """Check all groups before changing sources or writing files."""
        self._blocked_update_sources.clear()
        self._errors = [e for e in self._errors if not isinstance(e, UpdateGroupError)]
        allowed_ids = None if allowed is None else {id(data) for data in allowed}
        for group, members in self._update_groups.items():
            group_selected = allowed_ids is not None and any(
                data.new_version is not None and id(data) in allowed_ids
                for data in members
            )
            versions = []
            details = []
            for data in members:
                version = (
                    data.new_version.version
                    if data.new_version is not None
                    else data.selected_version
                )
                if data.check_failed:
                    status = "check failed"
                elif (
                    group_selected
                    and allowed_ids is not None
                    and data.new_version is not None
                    and id(data) not in allowed_ids
                ):
                    status = "update excluded by selection"
                elif not version or not version.strip():
                    status = "no selected version (unchecked, skipped or no candidate)"
                else:
                    status = repr(version)
                    versions.append(version)
                details.append(f"{data} ({data.source_path}): {status}")
            if len(versions) == len(members) and len(set(versions)) == 1:
                continue
            self._blocked_update_sources.update(id(data) for data in members)
            error = UpdateGroupError(
                f"Update group {group!r} blocked: all members must successfully "
                "select the same nonempty version; " + "; ".join(details)
            )
            self._errors.append(error)
            log.error(error)

    def get_external_data(self, only_type=None) -> list[ExternalBase]:
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

    def get_report_errors(self) -> list[Exception]:
        """Include skipped manifest files so partial coverage is visible."""
        return [*self.get_errors(), *self._incomplete_manifests]

    def get_source_report(self) -> list[dict[str, t.Any]]:
        """Snapshot detection results before update_manifests mutates sources."""
        planned = {id(data) for data in self._select_updates()}

        def find_pointer(node, target, pointer=""):
            if node is target:
                return pointer
            items = (
                node.items()
                if isinstance(node, dict)
                else (enumerate(node) if isinstance(node, list) else [])
            )
            for key, value in items:
                escaped = str(key).replace("~", "~0").replace("/", "~1")
                found = find_pointer(value, target, f"{pointer}/{escaped}")
                if found is not None:
                    return found
            return None

        results = []
        for data in self.get_external_data():
            attempted = self._source_checks.get(id(data), [])
            reason = None
            if data.check_failed:
                status = "failed"
            elif data.new_version is not None:
                status = "update_available" if id(data) in planned else "blocked"
                if status == "blocked":
                    reason = (
                        "update_group"
                        if id(data) in self._blocked_update_sources
                        else "update_policy"
                    )
            elif data.State.BROKEN in data.state:
                status = "failed"
                reason = "broken_url"
            elif self._filter_type is not None and data.type != self._filter_type:
                status, reason = "unchecked", "filtered"
            elif not data.checker_data.get("type"):
                status, reason = "unchecked", "no_update_checker"
            elif not attempted:
                status, reason = "unchecked", "no_applicable_checker"
            elif data.State.LATEST in data.state:
                status = "checked"
            else:
                status, reason = "unchecked", "no_candidate"
            results.append(
                {
                    "manifest": os.path.relpath(
                        data.source_path, self._root_manifest_dir
                    ),
                    "source_pointer": find_pointer(
                        self._manifest_contents[data.source_path], data.source
                    ),
                    "module": str(data.module) if data.module else None,
                    "type": data.type.value,
                    "checker_type": data.checker_data.get("type"),
                    "checkers_run": list(attempted),
                    "status": status,
                    "reason": reason,
                    "state": data.state.name,
                    "current": data.current_version.json,
                    "candidate": data.new_version.json if data.new_version else None,
                    "selected_version": data.selected_version,
                    "update_group": data.checker_data.get("update-group"),
                    "errors": [
                        {"type": type(error).__name__, "message": str(error)}
                        for error in self._source_errors.get(id(data), [])
                    ],
                }
            )
        return results

    def get_errors(
        self,
        only_type: type[Exception] | None = None,
    ) -> list[Exception]:
        """Return a list of errors occurred while checking/updating the manifest"""

        return [
            e for e in self._errors if only_type is None or isinstance(e, only_type)
        ]

    def get_outdated_external_data(
        self, *, include_blocked: bool = True
    ) -> list[ExternalBase]:
        """Returns a list of the outdated external data

        Outdated external data are the ones that either are broken
        (unreachable URL) or have a new version.
        Pass include_blocked=False to exclude rejected groups from automerge.
        """
        return [
            data
            for data in self.get_external_data()
            if (data.State.BROKEN in data.state or data.new_version)
            and (include_blocked or self._can_update(data))
        ]

    def _can_update(self, data: ExternalBase) -> bool:
        return (
            data.new_version is not None
            and id(data) not in self._blocked_update_sources
            and (self._allowed_updates is None or id(data) in self._allowed_updates)
        )

    def get_applied_external_data(self) -> list[ExternalBase]:
        return [data for data in self.get_external_data() if self._can_update(data)]

    def get_planned_updates(
        self, allowed: list[ExternalBase] | None = None
    ) -> list[ExternalBase]:
        """Select writes without mutating sources or requiring GitHub access."""
        self._validate_update_groups(allowed)
        return self._select_updates(allowed)

    def _select_updates(
        self, allowed: list[ExternalBase] | None = None
    ) -> list[ExternalBase]:
        sources = self.get_external_data()
        allowed_ids = None if allowed is None else {id(data) for data in allowed}
        updates = [
            data
            for data in sources
            if data.new_version is not None
            and id(data) not in self._blocked_update_sources
            and (allowed_ids is None or id(data) in allowed_ids)
        ]
        if self.opts.require_important_update:
            important = {
                id(data)
                for data in sources
                if data.checker_data.get(IMPORTANT_SRC_PROP)
                or (
                    data.checker_data.get(MAIN_SRC_PROP)
                    and data.checker_data.get(IMPORTANT_SRC_PROP) is not False
                )
            }
            if important and not any(
                id(data) in important
                and (
                    data.has_version_changed
                    or (
                        data.new_version is not None
                        and data.State.BROKEN in data.state
                        and not data.current_version.matches(data.new_version)
                    )
                )
                for data in updates
            ):
                return []
        return updates

    def _update_manifest(self, path, datas, changes):
        path_has_changes = False
        for data in datas:
            if not self._can_update(data):
                continue

            assert data.new_version is not None

            data_new_ver_version = data.new_version.version

            source_update = SourceUpdate.from_source(data)
            data.update()
            self.source_updates.append(source_update)
            if data_new_ver_version is not None:
                message = (
                    f"{data.module}: Update {data.filename} to {data_new_ver_version}"
                )
            else:
                message = f"{data.module}: Update {data.filename}"

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

        selected = self._main_sources()
        if not selected:
            log.error(
                "No main source configured and no external source in %s. "
                "Not updating appdata",
                self._root_manifest_path,
            )
            return
        selected_data = selected[0]
        log.info("Selected upstream source: %s", selected_data)

        if id(selected_data) in self._blocked_update_sources:
            log.info(
                "Main source belongs to a blocked update group; not updating appdata"
            )
            return

        release_url_template = selected_data.checker_data.get(RELEASE_URL_TEMPLATE_PROP)
        last_update = selected_data.new_version

        if self._can_update(selected_data) and selected_data.has_version_changed:
            assert last_update is not None
            log.info("Version changed, adding release to %s", appdata)
            if last_update.timestamp is None:
                log.warning("Using current time in appdata release")
                timestamp = datetime.datetime.now(tz=datetime.timezone.utc)
            else:
                timestamp = last_update.timestamp
            try:
                version_str = last_update.version
                assert version_str is not None
                add_release_to_file(
                    appdata,
                    version_str,
                    timestamp.strftime("%F"),
                    release_url_template,
                    release_url=selected_data.new_release_url,
                )
            except XMLSyntaxError as err:
                raise AppdataLoadError from err
        else:
            log.debug("Version didn't change, not adding release")

    def update_manifests(self, allowed: list[ExternalBase] | None = None) -> list[str]:
        """Updates references to external data in manifests.

        If require_important_update is True, only update the manifest if at least one
        source with IMPORTANT_SRC_PROP or MAIN_SRC_PROP received an update.
        """
        # We want a list, without duplicates; Python provides an
        # insertion-order-preserving dictionary so we use that.
        changes: dict[str, t.Any]
        changes = OrderedDict()
        self.source_updates = []
        self._allowed_updates = {id(data) for data in self.get_planned_updates(allowed)}
        if not self._allowed_updates:
            log.info("No eligible source updates, not updating manifest")
            return []

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

        return list(changes)
