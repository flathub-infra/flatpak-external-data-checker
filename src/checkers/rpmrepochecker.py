import io
import gzip
from datetime import datetime
import logging
from distutils.version import LooseVersion
from xml.etree.ElementTree import Element

from yarl import URL
from defusedxml import ElementTree

from ..lib.externaldata import ExternalBase, ExternalFile
from ..lib.checksums import MultiDigest
from . import Checker


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
    def _file_from_xml(cls, rpm: Element, repo_root: URL):
        def child_prop(child: str, prop: str):
            child_el = rpm.find(child, cls._XMLNS)
            assert child_el is not None, child
            value = child_el.get(prop)
            assert value is not None, prop
            return value

        digests = {}
        for cs_elem in rpm.findall("checksum", cls._XMLNS):
            cs_elem_type = cs_elem.get("type")
            if cs_elem_type:
                digests[cs_elem_type] = cs_elem.text

        return ExternalFile(
            url=str(repo_root.join(URL(child_prop("location", "href")))),
            checksum=MultiDigest.from_source(digests),
            size=int(child_prop("size", "archive")),
            version=child_prop("version", "ver"),
            timestamp=datetime.utcfromtimestamp(int(child_prop("time", "file"))),
        )

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        repo_root = URL(external_data.checker_data["root"].rstrip("/") + "/")
        package_name = external_data.checker_data["package-name"]
        package_arch = external_data.arches[0]

        repomd_xml_url = repo_root.join(URL("repodata/repomd.xml"))
        log.debug("Loading %s", repomd_xml_url)
        async with self.session.get(repomd_xml_url) as resp:
            repomd_xml = ElementTree.fromstring(await resp.text())

        primary_location_el = repomd_xml.find(
            'repo:data[@type="primary"]/repo:location', self._XMLNS
        )

        primary_xml_url = repo_root.join(URL(primary_location_el.get("href")))
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
            external_files.append(self._file_from_xml(package_el, repo_root))

        new_version = max(external_files, key=lambda e: LooseVersion(e.version))

        external_data.set_new_version(new_version)
