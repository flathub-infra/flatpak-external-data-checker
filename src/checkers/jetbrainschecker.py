import datetime
import logging

from ..lib.externaldata import ExternalBase, ExternalFile
from ..lib.checksums import MultiDigest
from ..lib.checkers import Checker

log = logging.getLogger(__name__)

_JB_ARCH_MAP = {
    "x86_64": "linux",
    "aarch64": "linuxARM64",
}


class JetBrainsChecker(Checker):
    CHECKER_DATA_TYPE = "jetbrains"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            # TODO: add enum here
            "release-type": {"type": "string"},
        },
        "required": ["code"],
    }

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        code = external_data.checker_data["code"]
        release_type = external_data.checker_data.get("release-type", "release")

        url = "https://data.services.jetbrains.com/products/releases"
        query = {"code": code, "latest": "true", "type": release_type}

        async with self.session.get(url, params=query) as response:
            result = await response.json()
            data = result[code][0]

        arch = external_data.arches[0]
        release = data["downloads"][f"linux{DOWNLOAD_NODE_SUFFIX[arch]}"]

        async with self.session.get(release["checksumLink"]) as response:
            result = await response.text()
            checksum = result.split(" ")[0]

        new_version = ExternalFile(
            url=release["link"],
            checksum=MultiDigest(sha256=checksum),
            size=release["size"],
            version=data["version"],
            timestamp=datetime.datetime.strptime(data["date"], "%Y-%m-%d"),
        )

        external_data.set_new_version(new_version)
