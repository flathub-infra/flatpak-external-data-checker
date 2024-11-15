import base64
import logging
import re
import typing as t

import aiohttp

from ..lib import NETWORK_ERRORS
from ..lib.externaldata import (
    ExternalBase,
    ExternalData,
    ExternalGitRepo,
    ExternalGitRef,
)
from ..lib.utils import get_extra_data_info_from_url
from ..lib.errors import CheckerMetadataError, CheckerFetchError
from . import Checker

log = logging.getLogger(__name__)


class Component:
    NAME: str
    DATA_CLASS: t.Type[ExternalBase]

    def __init__(
        self,
        session: aiohttp.ClientSession,
        external_data: ExternalBase,
        latest_version: str,
    ) -> None:
        self.session = session
        self.external_data = external_data
        self.latest_version = latest_version

        assert latest_version is not None

    async def check(self) -> None:
        raise NotImplementedError

    async def update_external_source_version(self, latest_url):
        assert latest_url is not None

        try:
            new_version = await get_extra_data_info_from_url(latest_url, self.session)
        except NETWORK_ERRORS as err:
            raise CheckerFetchError from err
        else:
            new_version = new_version._replace(  # pylint: disable=no-member
                version=self.latest_version
            )
            self.external_data.set_new_version(new_version)


class ChromiumComponent(Component):
    NAME = "chromium"
    DATA_CLASS = ExternalData

    _URL_FORMAT = (
        "https://commondatastorage.googleapis.com"
        "/chromium-browser-official/chromium-{version}.tar.xz"
    )
    # https://groups.google.com/a/chromium.org/g/chromium-packagers/c/wjv9UKg2u4w/m/SwSvLazmCAAJ
    _GENTOO_URL_FORMAT = (
        "https://chromium-tarballs.distfiles.gentoo.org/chromium-{version}.tar.xz"
    )

    async def check(self) -> None:
        assert isinstance(self.external_data, ExternalData)

        try:
            latest_url = self._URL_FORMAT.format(version=self.latest_version)
            await self.update_external_source_version(latest_url)
        except CheckerFetchError as err:
            if (
                isinstance(err.__cause__, aiohttp.ClientResponseError)
                and err.__cause__.status == 404
            ):
                log.error(
                    "Chromium tarball is missing (falling back to alternate URL): %s",
                    err,
                )
                latest_url = self._GENTOO_URL_FORMAT.format(version=self.latest_version)
                await self.update_external_source_version(latest_url)
            else:
                raise


class LLVMComponent(Component):
    class Version(t.NamedTuple):
        revision: str
        sub_revision: str

    _UPDATE_PY_URL_FORMAT = (
        "https://chromium.googlesource.com/chromium/src/+"
        "/{version}/tools/clang/scripts/update.py"
    )

    _UPDATE_PY_PARAMS = {"format": "TEXT"}

    _CLANG_REVISION_RE = re.compile(r"CLANG_REVISION = '(.*)'")
    _CLANG_SUB_REVISION_RE = re.compile(r"CLANG_SUB_REVISION = (\d+)")

    async def get_llvm_version(self) -> "LLVMComponent.Version":
        url = self._UPDATE_PY_URL_FORMAT.format(version=self.latest_version)
        async with self.session.get(url, params=self._UPDATE_PY_PARAMS) as response:
            result = await response.text()

        update_py = base64.b64decode(result).decode("utf-8")

        revision_match = self._CLANG_REVISION_RE.search(update_py)
        assert revision_match is not None, url

        sub_revision_match = self._CLANG_SUB_REVISION_RE.search(update_py)
        assert sub_revision_match is not None, url

        return LLVMComponent.Version(
            revision_match.group(1), sub_revision_match.group(1)
        )


class LLVMGitComponent(LLVMComponent):
    NAME = "llvm-git"
    DATA_CLASS = ExternalGitRepo

    _LLVM_REPO_URL = "https://github.com/llvm/llvm-project"

    async def check(self) -> None:
        assert isinstance(self.external_data, ExternalGitRepo)

        llvm_version = await self.get_llvm_version()

        new_version = ExternalGitRef(
            url=self.external_data.current_version.url,
            commit=llvm_version.revision,
            tag=None,
            branch=None,
            version=self.latest_version,
            timestamp=None,
        )
        self.external_data.set_new_version(new_version)


class LLVMPrebuiltComponent(LLVMComponent):
    NAME = "llvm-prebuilt"
    DATA_CLASS = ExternalData

    _PREBUILT_URL_FORMAT = (
        "https://commondatastorage.googleapis.com"
        "/chromium-browser-clang/Linux_x64/clang-{revision}-{sub_revision}.tar.xz"
    )

    async def check(self) -> None:
        assert isinstance(self.external_data, ExternalData)

        llvm_version = await self.get_llvm_version()

        latest_url = self._PREBUILT_URL_FORMAT.format(
            revision=llvm_version.revision, sub_revision=llvm_version.sub_revision
        )
        await self.update_external_source_version(latest_url)


class ChromiumChecker(Checker):
    CHECKER_DATA_TYPE = "chromium"
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    _COMPONENTS = {
        c.NAME: c for c in (ChromiumComponent, LLVMGitComponent, LLVMPrebuiltComponent)
    }

    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "component": {
                "type": "string",
                "enum": list(_COMPONENTS),
            },
        },
        "required": ["component"],
    }

    _CHROMIUM_VERSIONS_URL = "https://chromiumdash.appspot.com/fetch_releases"
    _CHROMIUM_VERSIONS_PARAMS = {"platform": "Linux", "channel": "Stable", "num": "1"}

    async def _get_latest_chromium(self) -> str:
        async with self.session.get(
            self._CHROMIUM_VERSIONS_URL, params=self._CHROMIUM_VERSIONS_PARAMS
        ) as response:
            result = await response.json()

        assert len(result) == 1, result
        return result[0]["version"]

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        component_name = external_data.checker_data.get(
            "component", ChromiumComponent.NAME
        )

        component_class = self._COMPONENTS[component_name]
        if not isinstance(external_data, component_class.DATA_CLASS):
            raise CheckerMetadataError(
                f"Invalid source type for component {component_name}"
            )

        latest_version = await self._get_latest_chromium()
        component = component_class(self.session, external_data, latest_version)
        await component.check()
