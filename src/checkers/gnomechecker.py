import logging
import re
import typing as t

from enum import StrEnum
from yarl import URL

from ..lib import OPERATORS_SCHEMA, NETWORK_ERRORS
from ..lib.errors import CheckerQueryError
from ..lib.externaldata import ExternalBase, ExternalFile
from ..lib.checksums import MultiDigest
from ..lib.utils import filter_versions
from . import Checker

log = logging.getLogger(__name__)

GNOME_MIRROR = URL("https://download.gnome.org/")


class VersionScheme(StrEnum):
    DEFAULT = "default"
    ODD_MINOR_IS_UNSTABLE = "odd-minor-is-unstable"


def _parse_checksums(text: str) -> t.Dict[str, str]:
    result = {}
    for line in text.splitlines():
        digest, filename = line.strip().split(maxsplit=1)
        result[filename] = digest
    return result


# Checkes whether any component of the version contains either alpha, beta or
# rc.
def _contains_keyword(version: str) -> bool:
    return any(kw in version for kw in ["alpha", "beta", "rc"])


def _is_stable(
    version: str,
    scheme: VersionScheme = VersionScheme.DEFAULT,
) -> bool:
    ver_list = version.split(".")
    if len(ver_list) < 2:
        # Single number, e.g. "41"
        return True
    major, minor = ver_list[:2]
    if scheme == VersionScheme.DEFAULT:
        if any(_contains_keyword(x) for x in ver_list[1:]):
            return False
    elif scheme == VersionScheme.ODD_MINOR_IS_UNSTABLE:
        if len(ver_list) >= 2:
            return (int(minor) % 2) == 0

    # XXX If we didn't see any indication that the version is a prerelease,
    # assume it's a normal (stable) release
    return True


class GNOMEChecker(Checker):
    CHECKER_DATA_TYPE = "gnome"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "stable-only": {"type": "boolean"},
            "version-scheme": {
                "type": "string",
                "enum": [
                    VersionScheme.DEFAULT,
                    VersionScheme.ODD_MINOR_IS_UNSTABLE,
                ],
            },
            "versions": OPERATORS_SCHEMA,
        },
        "required": ["name"],
    }

    async def check(self, external_data: ExternalBase):
        project_name = external_data.checker_data["name"]
        stable_only = external_data.checker_data.get("stable-only", True)
        constraints = external_data.checker_data.get("versions", {}).items()
        version_scheme = external_data.checker_data.get(
            "version-scheme", VersionScheme.DEFAULT
        )
        assert isinstance(project_name, str)

        proj_url = GNOME_MIRROR / "sources" / project_name
        try:
            async with self.session.get(proj_url / "cache.json") as cache_resp:
                # Some mirrors may sand invalid content-type; don't require it to be
                # application/json
                cache_json = await cache_resp.json(content_type=None)
        except NETWORK_ERRORS as err:
            raise CheckerQueryError from err
        _, downloads, versions, _ = cache_json

        filtered_versions = versions[project_name]
        if constraints:
            filtered_versions = filter_versions(filtered_versions, constraints)

        def _is_stable_wrapper(version):
            return _is_stable(version, version_scheme)

        try:
            if stable_only:
                try:
                    latest_version = list(
                        filter(_is_stable_wrapper, filtered_versions)
                    )[-1]
                except IndexError:
                    latest_version = filtered_versions[-1]
                    log.warning(
                        "Couldn't find any stable version for %s, selecting latest %s",
                        project_name,
                        latest_version,
                    )
            else:
                latest_version = filtered_versions[-1]
        except IndexError as e:
            raise CheckerQueryError("No matching versions") from e

        proj_files = downloads[project_name][latest_version]

        tarball_type, tarball_path = next(
            (prop, proj_files[prop])
            for prop in ["tar.xz", "tar.bz2", "tar.gz"]
            if prop in proj_files
        )

        checksum_path = proj_files.get(
            "sha256sum",
            re.sub(f"\\.{tarball_type}$", ".sha256sum", tarball_path),
        )

        async with self.session.get(proj_url / checksum_path) as cs_resp:
            checksums = _parse_checksums(await cs_resp.text())
        checksum = checksums[tarball_path.split("/")[-1]]

        new_version = ExternalFile(
            url=str(proj_url / tarball_path),
            checksum=MultiDigest(sha256=checksum),
            size=None,
            version=latest_version,
            timestamp=None,
            changelog_url=None,
        )

        external_data.set_new_version(new_version)
