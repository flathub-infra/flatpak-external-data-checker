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
from collections import namedtuple
from enum import Enum
import typing as t

import os
import logging
import subprocess

from . import utils


log = logging.getLogger(__name__)


class ExternalBase(abc.ABC):
    """
    Abstract base for remote data sources, such as file or VCS repo
    """

    Type = Enum("Type", "EXTRA_DATA FILE ARCHIVE GIT")

    TYPES = {
        "file": Type.FILE,
        "archive": Type.ARCHIVE,
        "extra-data": Type.EXTRA_DATA,
        "git": Type.GIT,
    }

    class State(Enum):
        UNKNOWN = 0
        VALID = 1 << 1  # URL is reachable
        BROKEN = 1 << 2  # URL couldn't be reached

    current_version: t.Union[ExternalFile, ExternalGitRef]
    new_version: t.Optional[t.Union[ExternalFile, ExternalGitRef]]

    @classmethod
    def from_source(cls, source_path, source):
        url = source.get("url")
        data_type = cls.TYPES.get(source.get("type"))
        if url is None or data_type is None:
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


class ExternalFile(
    namedtuple("ExternalFile", ("url", "checksum", "size", "version", "timestamp"))
):
    __slots__ = ()

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

    def __str__(self):
        version = self.new_version or self.current_version
        info = (
            "{filename}:\n"
            "  State:   {state}\n"
            "  Type:    {type}\n"
            "  URL:     {url}\n"
            "  SHA256:  {checksum}\n"
            "  Size:    {size}\n"
            "  Arches:  {arches}\n"
            "  Checker: {checker_data}".format(
                state=self.state.name,
                filename=self.filename,
                type=self.type.name,
                url=version.url,
                checksum=version.checksum,
                size=version.size,
                arches=self.arches,
                checker_data=self.checker_data,
            )
        )
        return info

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


class ExternalGitRef(
    namedtuple(
        "ExternalGitRef", ("url", "commit", "tag", "branch", "version", "timestamp")
    )
):
    __slots__ = ()

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

    def fetch_remote(self) -> ExternalGitRef:
        log.debug(
            "Retrieving commit from %s tag %s branch %s",
            self.url,
            self.tag,
            self.branch,
        )
        refs = utils.git_ls_remote(self.url)

        if self.tag is not None:
            got_commit = self._get_tagged_commit(refs, self.tag)
        elif self.branch is not None:
            got_commit = refs[f"refs/heads/{self.branch}"]
        else:
            got_commit = refs["HEAD"]

        return self._replace(commit=got_commit)

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
        self.type = self.TYPES["git"]
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
    SUPPORTED_DATA_CLASSES: t.List[t.Type[ExternalBase]] = [ExternalData]

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

    def check(self, external_data: t.Union[ExternalData, ExternalGitRepo]):
        pass
