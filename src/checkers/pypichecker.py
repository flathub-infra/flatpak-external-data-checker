import logging
from datetime import datetime
from distutils.version import StrictVersion, LooseVersion
import operator
import re
import typing as t

import requests

from ..lib.externaldata import Checker, ExternalFile

log = logging.getLogger(__name__)

PYPI_INDEX = "https://pypi.org/pypi"
OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}
BDIST_RE = re.compile(r"^(\S+)-(\d[\d\.\w]*\d)-(\S+)-(\S+)-(\S+).whl$")


def _version_matches(version: str, constraints: t.List[t.Tuple[str, str]]):
    if not constraints:
        return True
    for ver_oper, ver_limit in constraints:
        oper = OPERATORS[ver_oper]
        try:
            matches = oper(StrictVersion(version), StrictVersion(ver_limit))
        except ValueError:
            matches = oper(LooseVersion(version), LooseVersion(ver_limit))
        return matches
    return None


def _filter_downloads(
    pypy_releases: t.Dict[str, t.List[t.Dict]],
    constraints: t.List[t.Tuple[str, str]],
    packagetype: str,
) -> t.Generator[t.Tuple[str, t.Dict, datetime], None, None]:
    for pypi_version, pypi_downloads in pypy_releases.items():
        if not _version_matches(pypi_version, constraints):
            continue
        for download in pypi_downloads:
            if download["packagetype"] != packagetype:
                continue
            if download["python_version"] not in ["source", "py3", "py2.py3"]:
                continue
            date = datetime.fromisoformat(download["upload_time_iso_8601"].rstrip("Z"))
            yield (pypi_version, download, date)


class PyPIChecker(Checker):
    CHECKER_DATA_TYPE = "pypi"

    def __init__(self):
        self.session = requests.Session()

    def check(self, external_data):
        package_name = external_data.checker_data["name"]
        package_type = external_data.checker_data.get("packagetype", "sdist")
        if "versions" in external_data.checker_data:
            constraints = [(o, l) for o, l in external_data.checker_data["versions"]]
        else:
            constraints = []

        with self.session.get(f"{PYPI_INDEX}/{package_name}/json") as response:
            response.raise_for_status()
            pypi_data = response.json()

        if constraints:
            releases = pypi_data["releases"]
        else:
            releases = {pypi_data["info"]["version"]: pypi_data["urls"]}

        downloads = sorted(
            _filter_downloads(releases, constraints, package_type),
            key=lambda r: r[2],
        )

        try:
            pypi_version, pypi_download, pypi_date = downloads[-1]
        except IndexError:
            log.error("Couldn't find %s for package %s", package_type, package_name)
            return

        new_version = ExternalFile(
            url=pypi_download["url"],
            checksum=pypi_download["digests"]["sha256"],
            size=pypi_download["size"],
            version=pypi_version,
            timestamp=pypi_date,
        )
        external_data.set_new_version(new_version)
