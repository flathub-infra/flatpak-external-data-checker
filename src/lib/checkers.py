import logging
import typing as t

import aiohttp
from yarl import URL
import jsonschema

from . import NETWORK_ERRORS
from .externaldata import (
    ExternalBase,
    ExternalData,
    ExternalState,
    ExternalFile,
)
from .errors import (
    CheckerMetadataError,
    CheckerFetchError,
)
from .checksums import (
    MultiHash,
    MultiDigest,
)

log = logging.getLogger(__name__)


class Checker:
    CHECKER_DATA_TYPE: t.Optional[str] = None
    CHECKER_DATA_SCHEMA: t.Dict[str, t.Any]
    SUPPORTED_DATA_CLASSES: t.List[t.Type[ExternalBase]] = [ExternalData]
    session: aiohttp.ClientSession

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    # pylint: disable=unused-argument
    @classmethod
    def get_json_schema(self, data_class: t.Type[ExternalBase]) -> t.Dict[str, t.Any]:
        if not hasattr(self, "CHECKER_DATA_SCHEMA"):
            raise NotImplementedError(
                "If schema is not declared, this method must be overridden"
            )

        return self.CHECKER_DATA_SCHEMA

    @classmethod
    def should_check(cls, external_data: ExternalBase) -> bool:
        supported = any(
            isinstance(external_data, c) for c in cls.SUPPORTED_DATA_CLASSES
        )
        applicable = (
            cls.CHECKER_DATA_TYPE is not None
            and external_data.checker_data.get("type") == cls.CHECKER_DATA_TYPE
        )
        return applicable and supported

    async def validate_checker_data(self, external_data: ExternalBase):
        assert any(isinstance(external_data, c) for c in self.SUPPORTED_DATA_CLASSES)
        schema = self.get_json_schema(type(external_data))
        if not schema:
            return
        try:
            jsonschema.validate(external_data.checker_data, schema)
        except jsonschema.ValidationError as err:
            raise CheckerMetadataError("Invalid metadata schema") from err

    async def check(self, external_data: ExternalBase):
        raise NotImplementedError

    # Various helplers for checkers; assumed to be safely usable only from subclasses

    async def _complete_digests(
        self, url: t.Union[str, URL], digests: MultiDigest
    ) -> MultiDigest:
        """
        Re-download given `url`, verify it against given `digests`,
        and return a `MultiDigest` with all digest types set.
        """
        multihash = MultiHash()
        try:
            async with self.session.get(url) as resp:
                async for chunk, _ in resp.content.iter_chunks():
                    multihash.update(chunk)
        except NETWORK_ERRORS as err:
            raise CheckerFetchError from err
        new_digests = multihash.hexdigest()
        if new_digests != digests:
            raise CheckerFetchError(
                f"Checksum mismatch for {url}: "
                f"expected {digests}, got {new_digests}"
            )
        return new_digests

    async def _set_new_version(self, source: ExternalBase, new_version: ExternalState):
        """
        Set the `new_version` for `source`, ensuring common digest types are set.
        """
        if (
            isinstance(source, ExternalData) and isinstance(new_version, ExternalFile)
        ) and not (
            source.current_version.checksum.digests & new_version.checksum.digests
        ):
            log.warning(
                "Source %s: %s didn't get a %s digest; available digests were %s",
                source,
                self.__class__.__name__,
                source.current_version.checksum.digests,
                new_version.checksum.digests,
            )
            checksum = await self._complete_digests(
                new_version.url, new_version.checksum
            )
            new_version = new_version._replace(checksum=checksum)

        source.set_new_version(new_version)
