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

from __future__ import annotations

import abc
from enum import Enum
import datetime
import typing as t

import os
import logging

import aiohttp
import jsonschema

from . import utils


TIMEOUT_CONNECT = 60
TIMEOUT_TOTAL = 60 * 10
log = logging.getLogger(__name__)


class ExternalBase(abc.ABC):
    """
    Abstract base for remote data sources, such as file or VCS repo
    """

    class Type(Enum):
        EXTRA_DATA = "extra-data"
        FILE = "file"
        ARCHIVE = "archive"
        GIT = "git"

        def __str__(self):
            return self.value

    class State(Enum):
        UNKNOWN = 0
        VALID = 1 << 1  # URL is reachable
        BROKEN = 1 << 2  # URL couldn't be reached

    state: State
    type: Type
    filename: str
    current_version: t.Union[ExternalFile, ExternalGitRef]
    new_version: t.Optional[t.Union[ExternalFile, ExternalGitRef]]

    @classmethod
    def from_source(cls, source_path, source):
        try:
            url = source["url"]
        except KeyError:
            return None

        try:
            data_type = cls.Type(source.get("type"))
        except ValueError:
            return None

        if data_type == cls.Type.GIT:
            return ExternalGitRepoSource(source_path, source, url)

        return ExternalDataSource(source_path, source, data_type, url)

    @classmethod
    def from_sources(cls, source_path, sources):
        external_data = []

        for source in sources:
            if isinstance(source, str):
                continue

            data = cls.from_source(source_path, source)
            if data:
                external_data.append(data)

        return external_data

    def set_new_version(
        self, new_version: t.Union[ExternalFile, ExternalGitRef], is_update=True
    ):
        assert isinstance(new_version, type(self.current_version))

        if self.current_version.matches(new_version):  # type: ignore
            if is_update:
                log.debug("Source %s: no update found", self.filename)
            else:
                log.debug("Source %s: no remote data change", self.filename)
            self.state = self.State.VALID
        else:
            if is_update:
                log.info(
                    "Source %s: got new version %s", self.filename, new_version.version
                )
            else:
                log.warning("Source %s: remote data changed", self.filename)
                self.state = self.State.BROKEN
            self.new_version = new_version

    def __str__(self):
        return f"{self.type.value} {self.filename}"


class ExternalFile(t.NamedTuple):
    url: str
    checksum: t.Optional[str]
    size: t.Optional[int]
    version: t.Optional[str]
    timestamp: t.Optional[datetime.datetime]

    def matches(self, other: ExternalFile):
        return (
            self.url == other.url
            and self.checksum == other.checksum
            and (self.size is None or other.size is None or self.size == other.size)
        )


class ExternalData(ExternalBase):
    def __init__(
        self,
        data_type: ExternalBase.Type,
        source_path: str,
        filename: str,
        url: str,
        checksum: str = None,
        size: int = None,
        arches=[],
        checker_data: dict = None,
    ):
        self.source_path = source_path
        self.filename = filename
        self.arches = arches
        self.type = data_type
        assert data_type != self.Type.GIT
        self.checker_data = checker_data or {}
        assert size is None or isinstance(size, int)
        self.current_version: ExternalFile
        self.current_version = ExternalFile(url, checksum, size, None, None)
        self.new_version: t.Optional[ExternalFile]
        self.new_version = None
        self.state = ExternalData.State.UNKNOWN

    @abc.abstractmethod
    def update(self):
        """If self.new_version is not None, writes back the necessary changes to the
        original element from the manifest."""


class ExternalDataSource(ExternalData):
    def __init__(
        self,
        source_path: str,
        source: dict,
        data_type: ExternalBase.Type,
        url: str,
    ):
        name = (
            source.get("filename")
            or source.get("dest-filename")
            or os.path.basename(url)
        )

        sha256sum = source.get("sha256")
        size = source.get("size")
        checker_data = source.get("x-checker-data", {})
        arches = checker_data.get("arches") or source.get("only-arches") or ["x86_64"]

        super().__init__(
            data_type,
            source_path,
            name,
            url,
            sha256sum,
            size,
            arches,
            checker_data,
        )

        self.source = source

    def update(self):
        if self.new_version is not None:
            self.source["url"] = self.new_version.url
            self.source["sha256"] = self.new_version.checksum
            if self.source["type"] == "extra-data":
                assert self.new_version.size is not None
                self.source["size"] = self.new_version.size
            # Remove size property for non-extra-data sources
            elif "size" in self.source:
                log.warning(
                    "Removing size from source %s in %s",
                    self.filename,
                    self.source_path,
                )
                self.source.pop("size", None)


class ExternalGitRef(t.NamedTuple):
    url: str
    commit: t.Optional[str]
    tag: t.Optional[str]
    branch: t.Optional[str]
    version: t.Optional[str]
    timestamp: t.Optional[datetime.datetime]

    def _get_tagged_commit(self, refs: t.Dict[str, str], tag: str) -> str:
        annotated_tag_commit = refs.get(f"refs/tags/{tag}")
        lightweight_tag_commit = refs.get(f"refs/tags/{tag}^{{}}")
        # If either tag matched current commit, assume it's valid
        if self.commit is not None:
            if annotated_tag_commit == self.commit:
                return annotated_tag_commit
            if lightweight_tag_commit == self.commit:
                return lightweight_tag_commit
        # If neither matched current commit, prefer lightweight tag
        if lightweight_tag_commit is not None:
            return lightweight_tag_commit
        if annotated_tag_commit is not None:
            return annotated_tag_commit
        raise KeyError(f"refs/tags/{tag}")

    async def fetch_remote(self) -> ExternalGitRef:
        log.debug(
            "Retrieving commit from %s tag %s branch %s",
            self.url,
            self.tag,
            self.branch,
        )
        refs = await utils.git_ls_remote(self.url)

        if self.tag is not None:
            got_commit = self._get_tagged_commit(refs, self.tag)
        elif self.branch is not None:
            got_commit = refs[f"refs/heads/{self.branch}"]
        else:
            got_commit = refs["HEAD"]

        return self._replace(commit=got_commit)  # pylint: disable=no-member

    def matches(self, other: ExternalGitRef):
        return self.url == other.url and (
            # fmt: off
            (
                (self.commit is None and other.commit is None)
                or self.commit == other.commit
            )
            and (
                (self.tag is None and other.tag is None)
                or self.tag == other.tag
            )
            and (
                (self.branch is None and other.branch is None)
                or self.branch == other.branch
            )
            # fmt: on
        )


class ExternalGitRepo(ExternalBase):
    def __init__(
        self,
        source_path: str,
        repo_name: str,
        url: str,
        commit: str = None,
        tag: str = None,
        branch: str = None,
        arches=[],
        checker_data=None,
    ):
        self.source_path = source_path
        self.filename = repo_name
        self.arches = arches
        self.type = self.Type.GIT
        self.checker_data = checker_data or {}
        self.current_version: ExternalGitRef
        self.current_version = ExternalGitRef(url, commit, tag, branch, None, None)
        self.new_version: t.Optional[ExternalGitRef]
        self.new_version = None
        self.state = ExternalGitRepo.State.UNKNOWN


class ExternalGitRepoSource(ExternalGitRepo):
    def __init__(self, source_path: str, source: dict, url: str):
        repo_name = os.path.basename(url)
        commit = source.get("commit")
        tag = source.get("tag")
        branch = source.get("branch")
        checker_data = source.get("x-checker-data", {})
        arches = checker_data.get("arches") or source.get("only-arches") or ["x86_64"]

        super().__init__(
            source_path,
            repo_name,
            url,
            commit,
            tag,
            branch,
            arches,
            checker_data,
        )

        self.source = source

    def update(self):
        if self.new_version is not None:
            self.source["url"] = self.new_version.url
            self.source["commit"] = self.new_version.commit
            if self.new_version.tag is not None:
                self.source["tag"] = self.new_version.tag
            if self.new_version.branch is not None:
                self.source["branch"] = self.new_version.branch


class Checker:
    CHECKER_DATA_TYPE: t.Optional[str] = None
    CHECKER_DATA_SCHEMA: t.Dict[str, t.Any]
    SUPPORTED_DATA_CLASSES: t.List[t.Type[ExternalBase]] = [ExternalData]
    session: aiohttp.ClientSession

    def __init__(self):
        self.session = None

    async def __aenter__(self):
        log.debug("Starting HTTP session for %s", self)
        self.session = aiohttp.ClientSession(
            raise_for_status=True,
            timeout=aiohttp.ClientTimeout(connect=TIMEOUT_CONNECT, total=TIMEOUT_TOTAL),
        )
        await self.session.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        log.debug("Closing HTTP session for %s", self)
        await self.session.__aexit__(exc_type, exc_val, exc_tb)

    def get_json_schema(  # pylint: disable=unused-argument
        self, external_data: t.Union[ExternalData, ExternalGitRepo]
    ) -> t.Dict[str, t.Any]:
        if hasattr(self, "CHECKER_DATA_SCHEMA"):
            return self.CHECKER_DATA_SCHEMA
        raise NotImplementedError(
            "If schema is not declared, this method must be overridden"
        )

    def should_check(
        self, external_data: t.Union[ExternalData, ExternalGitRepo]
    ) -> bool:
        supported = any(
            isinstance(external_data, c) for c in self.SUPPORTED_DATA_CLASSES
        )
        applicable = (
            self.CHECKER_DATA_TYPE is not None
            and external_data.checker_data.get("type") == self.CHECKER_DATA_TYPE
        )
        return applicable and supported

    async def validate_checker_data(
        self, external_data: t.Union[ExternalData, ExternalGitRepo]
    ):
        assert any(isinstance(external_data, c) for c in self.SUPPORTED_DATA_CLASSES)
        schema = self.get_json_schema(external_data)
        if schema:
            jsonschema.validate(external_data.checker_data, schema)

    async def check(self, external_data: t.Union[ExternalData, ExternalGitRepo]):
        pass
