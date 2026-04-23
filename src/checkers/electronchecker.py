import base64
import logging
from datetime import datetime

import ruamel.yaml
from yarl import URL

from ..lib import NETWORK_ERRORS
from ..lib.checksums import MultiDigest
from ..lib.errors import CheckerQueryError
from ..lib.externaldata import (
    ExternalBase,
    ExternalData,
    ExternalFile,
)
from . import Checker
from .jsonchecker import parse_timestamp

yaml = ruamel.yaml.YAML(typ="safe")
log = logging.getLogger(__name__)


class ElectronChecker(Checker):
    CHECKER_DATA_TYPE = "electron-updater"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "format": "uri"},
        },
    }

    @staticmethod
    def _read_digests(obj: dict) -> MultiDigest:
        digests: dict[str, str] = {}
        for _k in MultiDigest._fields:
            if _k in obj:
                digests[_k] = base64.b64decode(obj[_k]).hex()
        return MultiDigest(**digests)

    async def check(self, external_data: ExternalBase):
        assert isinstance(external_data, ExternalData)

        if "url" in external_data.checker_data:
            metadata_url = URL(external_data.checker_data["url"])
        else:
            metadata_url = URL(external_data.current_version.url).join(
                URL("latest-linux.yml")
            )

        if self.robots_cache:
            await self.robots_cache.ensure_allowed(metadata_url)

        try:
            async with self.session.get(metadata_url) as resp:
                metadata = yaml.load(await resp.read())
        except NETWORK_ERRORS as err:
            raise CheckerQueryError from err

        if "files" in metadata:
            # Modern metadata format
            m_file = metadata["files"][0]
            file_url = metadata_url.join(URL(m_file["url"]))
            file_size = int(m_file["size"])
            checksum = self._read_digests(m_file)
        else:
            # Old electron-updater 1.x metadata format; no size property
            file_url = metadata_url.join(URL(metadata["path"]))
            file_size = None
            checksum = self._read_digests(metadata)

        timestamp: datetime | None
        if isinstance(metadata["releaseDate"], datetime):
            timestamp = metadata["releaseDate"]
        else:
            timestamp = parse_timestamp(metadata["releaseDate"])

        new_version = ExternalFile(
            url=str(file_url),
            checksum=checksum,
            size=file_size,
            version=metadata["version"],
            timestamp=timestamp,
        )

        await self._set_new_version(external_data, new_version)
