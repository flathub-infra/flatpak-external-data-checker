from urllib.parse import urljoin
import io
import gzip
from datetime import datetime
import logging
from distutils.version import LooseVersion
from xml.etree.ElementTree import Element
import typing as t

from defusedxml import ElementTree

from ..lib.externaldata import ExternalData, ExternalGitRepo, Checker, ExternalFile


log = logging.getLogger(__name__)


class RPMRepoChecker(Checker):
    CHECKER_DATA_TYPE = "rpm-repo"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "root": {"type": "string", "format": "uri"},
            "package-name": {"type": "string"},
        },
        "required": ["root", "package-name"],
    }
    _XMLNS = {
        "": "http://linux.duke.edu/metadata/common",
        "repo": "http://linux.duke.edu/metadata/repo",
        "rpm": "http://linux.duke.edu/metadata/rpm",
    }

    @classmethod
    def _get_child_prop(cls, parent: Element, child: str, prop: t.Optional[str] = None):
        child_el = parent.find(child, cls._XMLNS)
        assert child_el is not None, child
        if prop is None:
            value = child_el.text
        else:
            value = child_el.get(prop)
        assert value is not None, prop
        return value

    @classmethod
    def external_file_from_xml(cls, rpm: Element, repo_root: str):
        return ExternalFile(
            url=urljoin(repo_root, cls._get_child_prop(rpm, "location", "href")),
            checksum=cls._get_child_prop(rpm, 'checksum[@type="sha256"]'),
            size=int(cls._get_child_prop(rpm, "size", "archive")),
            version=cls._get_child_prop(rpm, "version", "ver"),
            timestamp=datetime.utcfromtimestamp(
                int(cls._get_child_prop(rpm, "time", "file"))
            ),
        )

    async def check(self, external_data: t.Union[ExternalData, ExternalGitRepo]):
        assert self.should_check(external_data)

        repo_root = external_data.checker_data["root"].rstrip("/") + "/"
        package_name = external_data.checker_data["package-name"]
        package_arch = external_data.arches[0]

        repomd_xml_url = urljoin(repo_root, "repodata/repomd.xml")
        log.debug("Loading %s", repomd_xml_url)
        async with self.session.get(repomd_xml_url) as resp:
            repomd_xml = ElementTree.fromstring(await resp.text())

        primary_location_el = repomd_xml.find(
            'repo:data[@type="primary"]/repo:location', self._XMLNS
        )

        primary_xml_url = urljoin(repo_root, primary_location_el.get("href"))
        log.debug("Loading %s", primary_xml_url)
        async with self.session.get(primary_xml_url) as resp:
            with io.BytesIO(await resp.read()) as compressed:
                with gzip.GzipFile(fileobj=compressed) as decompressed:
                    primary_xml = ElementTree.parse(decompressed)

        log.debug("Looking up package %s arch %s", package_name, package_arch)
        external_files = []
        for package_el in primary_xml.findall(
            f'package[name="{package_name}"][arch="{package_arch}"]', self._XMLNS
        ):
            external_files.append(self.external_file_from_xml(package_el, repo_root))

        new_version = max(external_files, key=lambda e: LooseVersion(e.version))

        external_data.set_new_version(new_version)
