import datetime
import logging
import hashlib

from ..lib.externaldata import ExternalBase, ExternalFile, Checker
from ..lib.checksums import MultiHash

log = logging.getLogger(__name__)


class SnapcraftChecker(Checker):
    CHECKER_DATA_TYPE = "snapcraft"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "channel": {"type": "string"},
        },
        "required": ["name", "channel"],
    }

    _arches = {"x86_64": "amd64", "aarch64": "arm64", "arm": "armhf", "i386": "i386"}
    _BLOCK_SIZE = 65536

    async def _get_digests(self, url: str, sha3_384: str):
        assert self.session is not None
        multihash = MultiHash()
        sha3 = hashlib.sha3_384()

        async with self.session.get(url) as response:
            async for data in response.content.iter_chunked(self._BLOCK_SIZE):
                multihash.update(data)
                sha3.update(data)

        if sha3.hexdigest() == sha3_384:
            return multihash.hexdigest()

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        name = external_data.checker_data["name"]
        channel = external_data.checker_data["channel"]

        url = f"http://api.snapcraft.io/v2/snaps/info/{name}"
        header = {"Snap-Device-Series": "16"}

        async with self.session.get(url, headers=header) as response:
            js = await response.json()

        data = [
            x
            for x in js["channel-map"]
            if x["channel"]["architecture"] == self._arches[external_data.arches[0]]
            and x["channel"]["name"] == channel
        ][0]

        if external_data.current_version.url != data["download"]["url"]:
            log.debug("Downloading file from %s; may take a while", url)
            multidigest = await self._get_digests(
                data["download"]["url"], data["download"]["sha3-384"]
            )

            if multidigest:
                new_version = ExternalFile(
                    url=data["download"]["url"],
                    checksum=multidigest,
                    size=data["download"]["size"],
                    version=data["version"],
                    timestamp=datetime.datetime.strptime(
                        data["channel"]["released-at"], "%Y-%m-%dT%H:%M:%S.%f%z"
                    ),
                )

                external_data.set_new_version(new_version)
