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
from enum import Enum, IntFlag
import datetime
import typing as t
import dataclasses
import logging
import asyncio

from yarl import URL
import jsonschema

from . import utils, FILE_URL_SCHEMES
from .errors import (
    CheckerFetchError,
    SourceLoadError,
    SourceUnsupported,
)
from .checksums import MultiDigest

_BM = t.TypeVar("_BM", bound="BuilderModule")
_BS = t.TypeVar("_BS", bound="BuilderSource")
_ES = t.TypeVar("_ES", bound="ExternalState")

CHECKER_DATA_SCHEMA_COMMON = {
    "type": "object",
    "properties": {
        "type": {"type": "string"},
        "is-main-source": {"type": "boolean"},
        "is-important": {"type": "boolean"},
        "arches": {
            "type": "array",
            "items": {"type": "string"},
        },
        "source-id": {"type": "string"},
        "parent-id": {"type": "string"},
    },
    "required": ["type"],
}

log = logging.getLogger(__name__)


@dataclasses.dataclass
class BuilderModule:
    name: str
    module: t.Dict[str, t.Any]
    module_path: str
    parent: t.Optional[BuilderModule] = None
    sources: t.List[BuilderSource] = dataclasses.field(default_factory=lambda: [])

    @classmethod
    def from_manifest(
        cls: t.Type[_BM],
        module_path: str,
        module: t.Dict,
        parent: t.Optional[BuilderModule] = None,
    ) -> _BM:
        return cls(
            name=module["name"],
            module=module,
            module_path=module_path,
            parent=parent,
        )

    def __str__(self):
        return self.name


class BuilderSourceMeta(abc.ABCMeta):
    """flatpak-builder source metaclass"""

    def __init__(cls, *args, **kwargs):
        super().__init__(*args, **kwargs)
        validator_cls = jsonschema.validators.validator_for(cls.SOURCE_SCHEMA)
        validator_cls.check_schema(cls.SOURCE_SCHEMA)
        cls.source_validator = validator_cls(cls.SOURCE_SCHEMA)


@dataclasses.dataclass
class BuilderSource(abc.ABC, metaclass=BuilderSourceMeta):
    """flatpak-builder source item"""

    SOURCE_SCHEMA = {
        "type": "object",
        "properties": {
            "type": {"type": "string"},
            "x-checker-data": CHECKER_DATA_SCHEMA_COMMON,
        },
        "required": ["type"],
    }

    class Type(str, Enum):
        EXTRA_DATA = "extra-data"
        FILE = "file"
        ARCHIVE = "archive"
        GIT = "git"

        def __str__(self):
            return self.value

    class State(IntFlag):
        UNKNOWN = 0
        # Current version state
        VALID = 1 << 1  # URL is reachable
        BROKEN = 1 << 2  # URL couldn't be reached
        # New version state
        LATEST = 1 << 3
        OUTDATED = 1 << 4

    type: t.ClassVar[Type]
    state: State
    filename: str
    arches: t.List[str]
    source: t.Dict[str, t.Any]
    source_path: str
    checker_data: t.Dict[str, t.Any]
    module: t.Optional[BuilderModule]
    parent: t.Optional[BuilderSource] = dataclasses.field(init=False, default=None)
    # fmt: off
    checked: asyncio.Event = dataclasses.field(init=False, default_factory=asyncio.Event)
    # fmt: on

    @classmethod
    def data_classes(cls: t.Type[_BS]) -> t.Dict[Type, t.Type[_BS]]:
        classes = {}
        if hasattr(cls, "type"):
            classes[cls.type] = cls
        for subclass in cls.__subclasses__():
            classes.update(subclass.data_classes())
        return classes

    @classmethod
    def from_source(
        cls: t.Type[_BS],
        source_path: str,
        source: t.Dict,
        module: t.Optional[BuilderModule] = None,
    ) -> _BS:
        try:
            cls.source_validator.validate(source, cls.SOURCE_SCHEMA)
        except jsonschema.ValidationError as err:
            raise SourceLoadError("Error reading source") from err

        try:
            data_type = cls.Type(source["type"])
        except ValueError as err:
            raise SourceUnsupported("Can't handle source") from err

        data_cls = cls.data_classes()[data_type]

        return data_cls.from_source_impl(source_path, source, module)

    @classmethod
    def from_source_impl(
        cls: t.Type[_BS],
        source_path: str,
        source: t.Dict,
        module: t.Optional[BuilderModule] = None,
    ) -> _BS:
        raise NotImplementedError

    @property
    def ident(self) -> str:
        if "source-id" in self.checker_data:
            return self.checker_data["source-id"]
        if self.module:
            index = [s for s in self.module.sources if s.type == self.type].index(self)
            return f"{self.module.name}-{self.type.value}-{index}"
        raise SourceLoadError("Can't get source id")

    def __str__(self):
        name = self.filename
        if self.module:
            name = f"{self.module.name}/{name}"
        return f"{self.type.value} {name}"

    def __repr__(self):
        return f"<{type(self).__name__} {self}>"


@dataclasses.dataclass(frozen=True)
class ExternalState(abc.ABC):
    url: str
    version: t.Optional[str]
    timestamp: t.Optional[datetime.datetime]

    def _replace(self: _ES, **kwargs) -> _ES:
        return dataclasses.replace(self, **kwargs)

    def _asdict(self) -> t.Dict[str, t.Any]:
        return dataclasses.asdict(self)

    @property
    def json(self) -> t.Dict[str, t.Any]:
        return self._asdict() | {
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }

    def matches(self: _ES, other: _ES) -> bool:
        raise NotImplementedError

    def is_same_version(self: _ES, other: _ES) -> bool:
        raise NotImplementedError


@dataclasses.dataclass
class ExternalBase(BuilderSource):
    """
    Abstract base for remote data sources, such as file or VCS repo
    """

    current_version: ExternalState
    new_version: t.Optional[ExternalState]

    @staticmethod
    def _name_from_url(url: t.Union[str, URL]) -> str:
        if not isinstance(url, URL):
            url = URL(url)
        return next(p for p in reversed(url.parts) if p)

    @classmethod
    def from_source(
        cls: t.Type[_BS],
        source_path: str,
        source: t.Dict,
        module: t.Optional[BuilderModule] = None,
    ) -> _BS:
        if not source.get("url"):
            raise SourceUnsupported('Data is not external: no "url" property')

        # FIXME: https://github.com/python/mypy/issues/9282
        return super().from_source(source_path, source, module)  # type: ignore

    def set_new_version(self, new_version: ExternalState, is_update: bool = True):
        assert isinstance(new_version, type(self.current_version))

        if self.current_version.matches(new_version):
            if is_update:
                log.debug("Source %s: no update found", self.filename)
                self.state |= self.State.LATEST
            else:
                log.debug("Source %s: no remote data change", self.filename)
            self.state |= self.State.VALID
        else:
            if self.current_version.is_same_version(new_version):
                log.warning("Source %s: remote data changed", self.filename)
                if is_update:
                    self.state |= self.State.LATEST
                self.state |= self.State.BROKEN
            elif is_update:
                log.info(
                    "Source %s: got new version %s", self.filename, new_version.version
                )
                self.state |= self.State.OUTDATED

            self.new_version = new_version

    @property
    def has_version_changed(self) -> bool:
        """Detect if some external data had a version change"""
        raise NotImplementedError

    def update(self):
        """If self.new_version is not None, writes back the necessary changes to the
        original element from the manifest."""
        raise NotImplementedError


@dataclasses.dataclass(frozen=True)
class ExternalFile(ExternalState):
    checksum: MultiDigest
    size: t.Optional[int]

    @property
    def json(self) -> t.Dict[str, t.Any]:
        return super().json | {
            "checksum": self.checksum._asdict(),  # pylint: disable=no-member
        }

    def matches(self, other: ExternalFile):
        for i in (self, other):
            assert i.checksum is None or isinstance(i.checksum, MultiDigest), i.checksum
        return (
            self.url == other.url
            and self.checksum == other.checksum
            and (self.size is None or other.size is None or self.size == other.size)
        )

    def is_same_version(self, other: ExternalFile):
        assert isinstance(other, type(self))
        return self.url == other.url


@dataclasses.dataclass
class ExternalData(ExternalBase):
    current_version: ExternalFile
    new_version: t.Optional[ExternalFile]

    @classmethod
    def from_source_impl(
        cls,
        source_path: str,
        source: t.Dict,
        module: t.Optional[BuilderModule] = None,
    ) -> ExternalData:
        data_type = cls.Type(source["type"])
        assert data_type == cls.type, data_type
        url_str = source["url"]
        url = URL(url_str)

        if url.scheme not in FILE_URL_SCHEMES:
            raise SourceUnsupported(f"Unsupported URL scheme {url.scheme}")

        name = (
            source.get("filename")
            or source.get("dest-filename")
            or cls._name_from_url(url)
        )

        checksum = MultiDigest.from_source(source)
        size = source.get("size")
        checker_data = source.get("x-checker-data", {})
        arches = checker_data.get("arches") or source.get("only-arches") or ["x86_64"]

        obj = cls(
            cls.State.UNKNOWN,
            name,
            arches,
            source,
            source_path,
            checker_data,
            module,
            ExternalFile(
                url=url_str,
                checksum=checksum,
                size=size,
                version=None,
                timestamp=None,
            ),
            None,
        )
        return obj

    def update(self):
        if self.new_version is not None:
            self.source["url"] = self.new_version.url
            self.new_version.checksum.update_source(self.source)
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

    @property
    def has_version_changed(self) -> bool:
        update = self.new_version
        return (
            update is not None
            and update.version is not None
            and isinstance(update, ExternalFile)
            and (
                update.url != self.current_version.url
                # TODO We can't reliably tell if the appimage version stayed the same
                # without downloading it, so just assume it changed
                or update.url.endswith(".AppImage")
            )
        )


@dataclasses.dataclass
class FileSource(ExternalData):
    type = ExternalBase.Type.FILE


@dataclasses.dataclass
class ArchiveSource(ExternalData):
    type = ExternalBase.Type.ARCHIVE


@dataclasses.dataclass
class ExtraDataSource(ExternalData):
    type = ExternalBase.Type.EXTRA_DATA


@dataclasses.dataclass(frozen=True)
class ExternalGitRef(ExternalState):
    commit: t.Optional[str]
    tag: t.Optional[str]
    branch: t.Optional[str]

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

        try:
            if self.tag is not None:
                got_commit = self._get_tagged_commit(refs, self.tag)
            elif self.branch is not None:
                got_commit = refs[f"refs/heads/{self.branch}"]
            else:
                got_commit = refs["HEAD"]
        except KeyError as err:
            raise CheckerFetchError(f"Ref not found in {self.url}") from err

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

    def is_same_version(self, other: ExternalGitRef):
        assert isinstance(other, type(self))
        # If only commit is set on both sides, compare it as a version indicator
        if all(p is None for p in [self.tag, self.branch, other.tag, other.branch]):
            return self.url == other.url and self.commit == other.commit
        # otherwise, compare tag an branch
        return (
            self.url == other.url
            and self.tag == other.tag
            and self.branch == other.branch
        )


@dataclasses.dataclass
class ExternalGitRepo(ExternalBase):
    type = ExternalBase.Type.GIT

    current_version: ExternalGitRef
    new_version: t.Optional[ExternalGitRef]

    @classmethod
    def from_source_impl(
        cls,
        source_path: str,
        source: t.Dict,
        module: t.Optional[BuilderModule] = None,
    ) -> ExternalGitRepo:
        data_type = cls.Type(source["type"])
        assert data_type == cls.type, data_type
        url = source["url"]
        repo_name = cls._name_from_url(url)
        commit = source.get("commit")
        tag = source.get("tag")
        branch = source.get("branch")
        checker_data = source.get("x-checker-data", {})
        arches = checker_data.get("arches") or source.get("only-arches") or ["x86_64"]

        obj = cls(
            cls.State.UNKNOWN,
            repo_name,
            arches,
            source,
            source_path,
            checker_data,
            module,
            ExternalGitRef(
                url=url,
                commit=commit,
                tag=tag,
                branch=branch,
                version=None,
                timestamp=None,
            ),
            None,
        )
        return obj

    @property
    def has_version_changed(self) -> bool:
        update = self.new_version
        return (
            update is not None
            and update.version is not None
            and isinstance(update, ExternalGitRef)
            and update.tag != self.current_version.tag
        )

    def update(self):
        if self.new_version is not None:
            self.source["url"] = self.new_version.url
            self.source["commit"] = self.new_version.commit
            if self.new_version.tag is not None:
                self.source["tag"] = self.new_version.tag
            if self.new_version.branch is not None:
                self.source["branch"] = self.new_version.branch
