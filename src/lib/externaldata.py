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
from collections import namedtuple
from enum import Enum

import os
import logging


log = logging.getLogger(__name__)


class ModuleData:
    def __init__(self, name, path, module):
        self.name = name
        self.path = path
        self.checker_data = module.get("x-checker-data", {})
        self.external_data = []


class ExternalFile(
    namedtuple("ExternalFile", ("url", "checksum", "size", "version", "timestamp"))
):
    __slots__ = ()

    def matches(self, other):
        return (
            self.url == other.url
            and self.checksum == other.checksum
            and (self.size is None or other.size is None or self.size == other.size)
        )


class ExternalData(abc.ABC):
    Type = Enum("Type", "EXTRA_DATA FILE ARCHIVE")

    TYPES = {
        "file": Type.FILE,
        "archive": Type.ARCHIVE,
        "extra-data": Type.EXTRA_DATA,
    }

    class State(Enum):
        UNKNOWN = 0
        VALID = 1 << 1  # URL is reachable
        BROKEN = 1 << 2  # URL couldn't be reached
        ADDED = 1 << 3  # New source added
        REMOVED = 1 << 4  # Source removed

    def __init__(
        self,
        data_type,
        source_path,
        source_parent,
        filename,
        url,
        checksum,
        size=None,
        arches=[],
        checker_data=None,
    ):
        self.source_path = source_path
        self.source_parent = source_parent
        self.filename = filename
        self.arches = arches
        self.type = data_type
        self.checker_data = checker_data or {}
        assert size is None or isinstance(size, int)
        self.current_version = ExternalFile(url, checksum, size, None, None)
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
    def __init__(self, source_path, source, sources, data_type, url):
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
            sources,
            name,
            url,
            sha256sum,
            size,
            arches,
            checker_data,
        )

        self.source = source

    @classmethod
    def from_source(cls, source_path, source, sources):
        url = source.get("url")
        data_type = cls.TYPES.get(source.get("type"))
        if url is None or data_type is None:
            return None

        return cls(source_path, source, sources, data_type, url)

    @classmethod
    def from_sources(cls, source_path, sources):
        external_data = []

        for source in sources:
            if isinstance(source, str):
                continue

            data = cls.from_source(source_path, source, sources)
            if data:
                external_data.append(data)

        return external_data

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

        if self.state == ExternalData.State.ADDED:
            self.source_parent.append(self.source)
        elif self.state == ExternalData.State.REMOVED:
            self.source_parent.remove(self.source)


class Checker:
    def check_module(self, module_data, external_data_list):
        pass

    def check(self, external_data):
        pass
