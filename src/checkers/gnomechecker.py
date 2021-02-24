import logging
from urllib.parse import urljoin
import typing as t

import requests

from ..lib.externaldata import Checker, ExternalData, ExternalFile

log = logging.getLogger(__name__)

GNOME_MIRROR = "https://download.gnome.org/"


def _parse_checksums(text: str) -> t.Dict[str, str]:
    result = {}
    for line in text.splitlines():
        digest, filename = line.strip().split(maxsplit=1)
        result[filename] = digest
    return result


def _is_stable(version: str) -> bool:
    major, minor = version.split(".")[:2]
    if int(major) >= 40:
        return minor not in ["alpha", "beta", "rc"]
    return (int(minor) % 2) == 0


class GNOMEChecker(Checker):
    CHECKER_DATA_TYPE = "gnome"

    def __init__(self):
        self.session = requests.Session()

    def check(self, external_data):
        external_data: ExternalData
        project_name = external_data.checker_data["name"]
        stable_only = external_data.checker_data.get("stable-only", True)
        assert isinstance(project_name, str)

        proj_url = urljoin(GNOME_MIRROR, f"sources/{project_name}/")
        with self.session.get(urljoin(proj_url, "cache.json")) as cache_resp:
            cache_resp.raise_for_status()
            cache_json = cache_resp.json()
        _, downloads, versions, _ = cache_json

        if stable_only:
            try:
                latest_version = list(filter(_is_stable, versions[project_name]))[-1]
            except IndexError:
                latest_version = versions[project_name][-1]
                log.warning(
                    "Couldn't find any stable version for %s, selecting latest %s",
                    project_name,
                    latest_version,
                )
        else:
            latest_version = versions[project_name][-1]

        proj_files = downloads[project_name][latest_version]

        tarball = next(
            proj_files[prop]
            for prop in ["tar.xz", "tar.bz2", "tar.gz"]
            if prop in proj_files
        )
        with self.session.get(urljoin(proj_url, proj_files["sha256sum"])) as cs_resp:
            cs_resp.raise_for_status()
            checksums = _parse_checksums(cs_resp.text)
        checksum = checksums[tarball.split("/")[-1]]

        new_version = ExternalFile(
            url=urljoin(proj_url, tarball),
            checksum=checksum,
            size=None,
            version=latest_version,
            timestamp=None,
        )

        external_data.set_new_version(new_version)
