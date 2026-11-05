# Debian Repo Checker: A checker that uses some metadata info from the
# manifest file in order to check whether there are newer versions of
# Debian package based external data modules.
#
# The contents of the x-checker-data for the module should be .e.g:
#   "x-checker-data": {
#                       "type": "debian-repo",
#                       "package-name": "YOUR_PACKAGE_NAME",
#                       "root": "ROOT_URL_TO_THE_DEBIAN_REPO",
#                       "dist": "DEBIAN_DIST",
#                       "component": "DEBIAN_COMPONENT"
#                     }
#
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

import asyncio
import contextlib
import datetime as dt
import logging
import os
import re
import tempfile
import typing as t
import urllib.parse

import apt
import apt_pkg

from ..lib import HTTP_CHUNK_SIZE, NETWORK_ERRORS
from ..lib.checksums import MultiDigest
from ..lib.errors import CheckerFetchError, CheckerQueryError
from ..lib.externaldata import ExternalBase, ExternalFile
from ..lib.utils import get_timestamp_from_url
from . import Checker

apt_pkg.init()

APT_NEEDED_DIRS = (
    "etc/apt/apt.conf.d",
    "etc/apt/preferences.d",
    "etc/apt/trusted.gpg.d",
    "var/lib/apt/lists/partial",
    "var/cache/apt/archives/partial",
    "var/log/apt",
    "var/lib/dpkg",
    "var/lib/dpkg/updates",
    "var/lib/dpkg/info",
)
DEB_HASH_MAP = {
    "SHA512": "sha512",
    "SHA256": "sha256",
    "SHA1": "sha1",
    "MD5Sum": "md5",
}

LOG = logging.getLogger(__name__)

# Only fetch standalone changelogs, never packages just to obtain a date.
MAX_CHANGELOG_SIZE = 1024 * 1024


def _changelog_timestamp(
    contents: bytes | bytearray, package_name: str, version: str
) -> dt.datetime | None:
    matching = False
    for line in contents.decode("utf-8", errors="replace").splitlines():
        header = re.match(r"^(\S+) \(([^)]+)\) ", line)
        if header:
            matching = header[1] == package_name and header[2] == version
        elif matching and (trailer := re.match(r"^ -- .+?  (.+)$", line)):
            try:
                return dt.datetime.strptime(
                    trailer[1], "%a, %d %b %Y %H:%M:%S %z"
                ).astimezone(dt.timezone.utc)
            except ValueError:
                return None
    return None


def _changelog_url(candidate: apt.package.Version) -> str | None:
    # python-apt's Package.get_changelog also recognizes Debian and Ubuntu,
    # but uses blocking I/O and does not bound the response size. Use the same
    # source-package layout with our shared HTTP session instead.
    origin = next(iter(candidate.origins), None)
    if origin is None:
        return None
    roots = {
        "Debian": "https://metadata.ftp-master.debian.org/changelogs",
        "Ubuntu": "https://changelogs.ubuntu.com/changelogs/pool",
    }
    root = roots.get(origin.origin)
    if root is None or not origin.component:
        return None
    name = candidate.source_name
    version = re.sub(r"^\d+:", "", candidate.source_version)
    prefix = name[:4] if name.startswith("lib") else name[:1]
    path = "/".join(
        urllib.parse.quote(part, safe="")
        for part in (origin.component, prefix, name, f"{name}_{version}", "changelog")
    )
    return f"{root}/{path}"


def read_deb_hashes(deb_hashes: apt_pkg.HashStringList) -> MultiDigest:
    digests: dict[str, str] = {}
    deb_hash: apt_pkg.HashString
    for deb_hash in deb_hashes:
        hash_type = DEB_HASH_MAP.get(deb_hash.hashtype)
        if hash_type:
            digests[hash_type] = deb_hash.hashvalue
    return MultiDigest.from_source(digests)


class LoggerAcquireProgress(apt.progress.text.AcquireProgress):
    def __init__(self, logger):
        class FileLike:
            def write(self, text):
                text = text.strip()
                if text:  # ignore write("\r")
                    logger.debug(text)

            def flush(self):
                pass

            # no fileno() to avoid SIGWINCH stuff

        super().__init__(FileLike())

    def pulse(self, owner):
        """Disable percentage reporting within files."""
        return apt.progress.base.AcquireProgress.pulse(self, owner)


class DebianRepoChecker(Checker):
    CHECKER_DATA_TYPE = "debian-repo"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "package-name": {"type": "string"},
            "root": {"type": "string"},
            "dist": {"type": "string"},
            "component": {"type": "string"},
            "source": {"type": "boolean"},
        },
        "required": ["package-name", "root", "dist"],
    }

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        LOG.debug("Checking %s", external_data.filename)
        package_name = external_data.checker_data["package-name"]
        root = external_data.checker_data["root"]
        dist = external_data.checker_data["dist"]
        component = external_data.checker_data.get("component", "")
        src_pkg = external_data.checker_data.get("source", False)

        if not component and not dist.endswith("/"):
            LOG.warning(
                '%s is missing Debian repo "component"; for an '
                'exact URL, "dist" must end with /',
                package_name,
            )
            return

        arch = self._translate_arch(external_data.arches[0])
        cache: apt.Cache
        with self._load_repo(root, dist, component, arch, src_pkg) as cache:
            if src_pkg:
                src_record = apt_pkg.SourceRecords()
                source_version, source_files = None, None
                while src_record.lookup(package_name):
                    source_version, source_files = src_record.version, src_record.files
                if not source_version:
                    raise CheckerQueryError(
                        f"No source package {package_name} in {root}"
                    )
                assert source_files is not None

                source_file = next(f for f in source_files if f.type == "tar")

                src_url = urllib.parse.urljoin(root.rstrip("/") + "/", source_file.path)

                new_version = ExternalFile(
                    url=src_url,
                    checksum=read_deb_hashes(source_file.hashes),
                    size=source_file.size,
                    # Strip epoch if present
                    version=re.sub(r"^\d+:", "", source_version),
                    timestamp=await get_timestamp_from_url(
                        src_url,
                        self.session,
                        robots_cache=self.robots_cache,
                    ),
                )
            else:
                try:
                    package = cache[package_name]
                except KeyError as err:
                    raise CheckerQueryError(
                        f"No binary package {package_name} in {root} for {arch}"
                    ) from err
                candidate = package.candidate
                if candidate is None:
                    raise CheckerQueryError(
                        f"No candidate for package {package_name} in {root} for {arch}"
                    )
                if not candidate.uri:
                    raise CheckerQueryError(
                        f"No URI for package {package_name} in {root} for {arch}"
                    )

                # Version.record exposes all Packages fields, including SHA512.
                # Unlike the individual hash properties, missing fields are safe
                # to query. Skip empty values, as PackageRecords.hashes does.
                # https://apt-team.pages.debian.net/python-apt/library/apt.package.html#apt.package.Version.record
                record = candidate.record
                checksum = MultiDigest.from_source(
                    {
                        hash_type: value
                        for field, hash_type in DEB_HASH_MAP.items()
                        if (value := record.get(field))
                    }
                )
                new_version = ExternalFile(
                    url=candidate.uri,
                    checksum=checksum,
                    size=candidate.size,
                    version=candidate.version,
                    timestamp=await self._get_timestamp_for_candidate(candidate),
                )

            external_data.set_new_version(new_version)

    def _translate_arch(self, arch: str) -> str:
        # Because architecture names in Debian differ from Flatpak's
        arches = {"x86_64": "amd64", "arm": "armel", "aarch64": "arm64"}
        return arches.get(arch, arch)

    async def _get_timestamp_for_candidate(
        self, candidate: apt.package.Version
    ) -> dt.datetime:
        assert candidate.uri
        changelog_url = _changelog_url(candidate)
        if changelog_url is not None:
            try:
                timestamp = await self._get_changelog_timestamp(
                    candidate, changelog_url
                )
                if timestamp is not None:
                    return timestamp
            except (
                CheckerFetchError,
                asyncio.TimeoutError,
                *NETWORK_ERRORS,
            ) as err:
                LOG.debug("Changelog unavailable for %s: %s", candidate.uri, err)
        return await get_timestamp_from_url(
            candidate.uri,
            self.session,
            robots_cache=self.robots_cache,
        )

    async def _get_changelog_timestamp(
        self, candidate: apt.package.Version, url: str
    ) -> dt.datetime | None:
        if self.robots_cache:
            await self.robots_cache.ensure_allowed(url)
        async with self.session.get(url) as response:
            response.raise_for_status()
            if self.robots_cache and str(response.url) != url:
                await self.robots_cache.ensure_allowed(str(response.url))
            if (
                response.content_length is not None
                and response.content_length > MAX_CHANGELOG_SIZE
            ):
                return None
            contents = bytearray()
            async for chunk in response.content.iter_chunked(HTTP_CHUNK_SIZE):
                if len(contents) + len(chunk) > MAX_CHANGELOG_SIZE:
                    return None
                contents.extend(chunk)
        return _changelog_timestamp(
            contents, candidate.source_name, candidate.source_version
        )

    @contextlib.contextmanager
    def _load_repo(
        self, deb_root: str, dist: str, component: str, arch: str, source=False
    ) -> t.Generator[apt.Cache, None, None]:
        with tempfile.TemporaryDirectory() as root:
            LOG.debug("Setting up apt directory structure in %s", root)

            for path in APT_NEEDED_DIRS:
                os.makedirs(os.path.join(root, path), exist_ok=True)

            # Create sources.list
            sources_list = os.path.join(root, "etc/apt/sources.list")
            with open(sources_list, "w") as f:
                # FIXME: import GPG key, remove 'trusted=yes' which skips GPG
                # verification
                if source:
                    f.write(f"deb-src [trusted=yes] {deb_root} {dist} {component}\n")
                else:
                    f.write(
                        f"deb [arch={arch} trusted=yes] {deb_root} {dist} {component}\n"
                    )

            # Create empty dpkg status
            dpkg_status = os.path.join(root, "var/lib/dpkg/status")
            with open(dpkg_status, "w") as f:
                pass

            # Setup generic configuration
            apt_pkg.init()
            apt_pkg.config.set("Dir", root)
            apt_pkg.config.set("Dir::State::status", dpkg_status)
            apt_pkg.config.set("Acquire::Languages", "none")
            progress = LoggerAcquireProgress(LOG)

            # Create a new cache with the appropriate architecture
            apt_pkg.config.set("APT::Architecture", arch)
            apt_pkg.config.set("APT::Architectures", arch)
            with contextlib.closing(apt.Cache()) as cache:
                try:
                    cache.update(progress)
                except apt.cache.FetchFailedException as err:
                    raise CheckerFetchError(
                        f"Failed to update Debian repository {deb_root} ({dist})"
                    ) from err
                cache.open()

                yield cache
