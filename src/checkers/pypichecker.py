import logging
import re
import typing as t
from datetime import datetime

from packaging.tags import Tag
from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import InvalidVersion
from packaging.version import Version as PackagingVersion
from yarl import URL

from ..lib import OPERATORS_SCHEMA
from ..lib.checksums import MultiDigest
from ..lib.errors import CheckerQueryError
from ..lib.externaldata import ExternalBase, ExternalFile
from ..lib.utils import expand_version_constraints, filter_versioned_items
from . import Checker

log = logging.getLogger(__name__)

PYPI_INDEX = "https://pypi.org/pypi"


# We want to keep the original version string on parsed version object
class Version(PackagingVersion):
    def __init__(self, version: str):
        super().__init__(version)
        self.orig_str = version


def _manylinux_baselines(
    tags: frozenset[Tag],
) -> dict[tuple[str, str, str], tuple[int, int]]:
    baselines: dict[tuple[str, str, str], tuple[int, int]] = {}
    legacy = {"manylinux1": (2, 5), "manylinux2010": (2, 12), "manylinux2014": (2, 17)}
    for tag in tags:
        prefix, _, arch = tag.platform.partition("_")
        if prefix in legacy:
            version = legacy[prefix]
        else:
            match = re.fullmatch(r"manylinux_(\d+)_(\d+)_(\w+)", tag.platform)
            if not match:
                continue
            version = (int(match[1]), int(match[2]))
            arch = match[3]
        key = (tag.interpreter, tag.abi, arch)
        baselines[key] = min(baselines.get(key, version), version)
    return baselines


def _filter_downloads(
    pypy_releases: dict[str, list[dict]],
    constraints: list[tuple[str, Version]],
    packagetype: str,
    stable_only: bool = False,
    wheel_tags: frozenset[Tag] = frozenset({Tag("py3", "none", "any")}),
) -> t.Generator[tuple[Version, dict, datetime], None, None]:
    source_baselines = _manylinux_baselines(wheel_tags)
    parsed_releases = []
    for version, downloads in pypy_releases.items():
        try:
            parsed_version = Version(version)
        except InvalidVersion:
            log.debug("Skipping invalid PyPI release version %r", version)
            continue
        parsed_releases.append((parsed_version, downloads))

    releases = filter_versioned_items(
        parsed_releases,
        constraints,
        to_version=lambda r: r[0],
        sort=True,
    )
    for pypi_version, pypi_downloads in releases:
        if stable_only and pypi_version.is_prerelease:
            continue
        for download in pypi_downloads:
            if download.get("yanked", False):
                continue
            if download["packagetype"] != packagetype:
                continue
            if download["packagetype"] == "bdist_wheel":
                try:
                    tags = parse_wheel_filename(download["filename"])[3]
                except InvalidWheelFilename:
                    log.debug(
                        "Skipping invalid wheel filename %r", download["filename"]
                    )
                    continue
                if source_baselines:
                    # A shared tag can hide a higher minimum glibc version.
                    baselines = _manylinux_baselines(tags)
                    if any(
                        key not in baselines or baselines[key] > version
                        for key, version in source_baselines.items()
                    ):
                        continue
                elif not tags.intersection(wheel_tags):
                    continue
            elif download["python_version"] not in ["source", "py3", "py2.py3"]:
                continue
            date = datetime.fromisoformat(download["upload_time_iso_8601"].rstrip("Z"))
            yield (pypi_version, download, date)


class PyPIChecker(Checker):
    CHECKER_DATA_TYPE = "pypi"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "packagetype": {"type": "string", "enum": ["sdist", "bdist_wheel"]},
            "versions": OPERATORS_SCHEMA,
            "stable-only": {"type": "boolean"},
        },
        "required": ["name"],
    }

    def __init__(self, *args, **kwargs):
        new_args = list(args)

        # /pypi/*/json is blocked
        if len(new_args) > 1:
            new_args[1] = None
        else:
            kwargs["robots_cache"] = None

        super().__init__(*new_args, **kwargs)

    async def check(self, external_data: ExternalBase):
        package_name = external_data.checker_data["name"]
        package_type = external_data.checker_data.get("packagetype", "sdist")
        constraints = [
            (o, Version(v))
            for o, v in expand_version_constraints(
                external_data.checker_data.get("versions", {})
            )
        ]
        stable_only = external_data.checker_data.get("stable-only", True)
        wheel_tags = frozenset({Tag("py3", "none", "any")})
        if package_type == "bdist_wheel" and isinstance(
            external_data.current_version, ExternalFile
        ):
            try:
                source_tags = parse_wheel_filename(
                    URL(external_data.current_version.url).name
                )[3]
                wheel_tags = frozenset(
                    tag for tag in source_tags if tag.interpreter != "py2"
                )
            except InvalidWheelFilename:
                pass

        async with self.session.get(f"{PYPI_INDEX}/{package_name}/json") as response:
            pypi_data = await response.json()

        downloads = []
        if stable_only and not constraints:
            releases = {pypi_data["info"]["version"]: pypi_data["urls"]}
            downloads = list(
                _filter_downloads(
                    releases, constraints, package_type, stable_only, wheel_tags
                )
            )
        if not downloads:
            downloads = list(
                _filter_downloads(
                    pypi_data["releases"],
                    constraints,
                    package_type,
                    stable_only,
                    wheel_tags,
                )
            )

        if not downloads:
            raise CheckerQueryError(
                f"Couldn't find {package_type} for package {package_name}"
            )
        pypi_version, pypi_download, pypi_date = downloads[-1]

        checksum = MultiDigest.from_source(pypi_download["digests"])

        new_version = ExternalFile(
            url=pypi_download["url"],
            checksum=checksum,
            size=pypi_download["size"],
            version=pypi_version.orig_str,
            timestamp=pypi_date,
        )
        external_data.set_new_version(new_version)
