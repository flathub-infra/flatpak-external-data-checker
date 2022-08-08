import logging
from datetime import datetime
import re
import typing as t

from packaging.version import Version as PackagingVersion

from ..lib import OPERATORS_SCHEMA
from ..lib.externaldata import ExternalFile, ExternalBase
from ..lib.checksums import MultiDigest
from ..lib.utils import filter_versioned_items
from ..lib.errors import CheckerQueryError
from ..lib.checkers import Checker

log = logging.getLogger(__name__)

PYPI_INDEX = "https://pypi.org/pypi"
BDIST_RE = re.compile(r"^(\S+)-(\d[\d\.\w]*\d)-(\S+)-(\S+)-(\S+).whl$")


# We want to keep the original version string on parsed version object
class Version(PackagingVersion):
    def __init__(self, version: str):
        super().__init__(version)
        self.orig_str = version


def _filter_downloads(
    pypy_releases: t.Dict[str, t.List[t.Dict]],
    constraints: t.List[t.Tuple[str, Version]],
    packagetype: str,
    stable_only: bool = False,
) -> t.Generator[t.Tuple[Version, t.Dict, datetime], None, None]:
    releases = filter_versioned_items(
        ((Version(v), d) for v, d in pypy_releases.items()),
        constraints,
        to_version=lambda r: r[0],
        sort=True,
    )
    for pypi_version, pypi_downloads in releases:
        if stable_only and pypi_version.pre:
            continue
        for download in pypi_downloads:
            if download["packagetype"] != packagetype:
                continue
            if download["python_version"] not in ["source", "py3", "py2.py3"]:
                continue
            if download["packagetype"] == "bdist_wheel":
                # Make sure we get only noarch wheels
                if not download["filename"].endswith("-any.whl"):
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

    async def check(self, external_data: ExternalBase):
        package_name = external_data.checker_data["name"]
        package_type = external_data.checker_data.get("packagetype", "sdist")
        constraints = [
            (o, Version(v))
            for o, v in external_data.checker_data.get("versions", {}).items()
        ]
        stable_only = external_data.checker_data.get("stable-only", True)

        async with self.session.get(f"{PYPI_INDEX}/{package_name}/json") as response:
            pypi_data = await response.json()

        if constraints:
            releases = pypi_data["releases"]
        else:
            releases = {pypi_data["info"]["version"]: pypi_data["urls"]}

        downloads = list(
            _filter_downloads(releases, constraints, package_type, stable_only)
        )

        try:
            pypi_version, pypi_download, pypi_date = downloads[-1]
        except IndexError as err:
            raise CheckerQueryError(
                f"Couldn't find {package_type} for package {package_name}"
            ) from err

        checksum = MultiDigest.from_source(pypi_download["digests"])

        new_version = ExternalFile(
            url=pypi_download["url"],
            checksum=checksum,
            size=pypi_download["size"],
            version=pypi_version.orig_str,
            timestamp=pypi_date,
        )
        external_data.set_new_version(new_version)
