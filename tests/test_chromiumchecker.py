import base64
import hashlib
import unittest
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from src.checkers.chromiumchecker import (
    ChromiumChecker,
    ChromiumComponent,
    LLVMGitComponent,
)
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerFetchError
from src.lib.externaldata import (
    ExternalBase,
    ExternalData,
    ExternalFile,
    ExternalGitRef,
)


class TestChromiumChecker(unittest.IsolatedAsyncioTestCase):
    CHROMIUM_VERSION = "148.0.7778.178"
    CLANG_REV = "llvmorg-23-init-5669-g8a0be0bc"
    CHROMIUM_URL = (
        "https://commondatastorage.googleapis.com/chromium-browser-official/"
        f"chromium-{CHROMIUM_VERSION}.tar.xz"
    )
    GENTOO_URL = (
        "https://chromium-tarballs.distfiles.gentoo.org/"
        f"chromium-{CHROMIUM_VERSION}-linux.tar.xz"
    )
    CLANG_URL = (
        "https://commondatastorage.googleapis.com/chromium-browser-clang/"
        f"Linux_x64/clang-{CLANG_REV}-4.tar.xz"
    )
    ARCHIVE_BODY = b"test archive contents"

    def source(self, component):
        source = {"x-checker-data": {"type": "chromium", "component": component}}
        if component == "llvm-git":
            source.update(
                type="git", url="https://github.com/llvm/llvm-project", commit="0" * 40
            )
        else:
            source.update(
                type="archive",
                url=f"https://example.invalid/{component}-old.tar.xz",
                sha256="0" * 64,
            )
        return ExternalBase.from_source("sources.json", source)

    def checker(self, downloads=None):
        downloads = downloads or {}
        requested_downloads = []

        def get(url, **kwargs):
            response = MagicMock()
            response.__aenter__ = AsyncMock(return_value=response)
            response.__aexit__ = AsyncMock(return_value=None)
            response.url = url
            response.headers = {"Last-Modified": "Wed, 01 Jul 2026 00:00:00 GMT"}
            if url == "https://chromiumdash.appspot.com/fetch_releases":
                self.assertEqual(
                    kwargs["params"],
                    {"platform": "Linux", "channel": "Stable", "num": "1"},
                )
                response.json = AsyncMock(
                    return_value=[{"version": self.CHROMIUM_VERSION}]
                )
            elif url == (
                "https://chromium.googlesource.com/chromium/src/+/"
                f"{self.CHROMIUM_VERSION}/tools/clang/scripts/update.py"
            ):
                self.assertEqual(kwargs["params"], {"format": "TEXT"})
                response.text = AsyncMock(
                    return_value=base64.b64encode(
                        (
                            f"CLANG_REVISION = '{self.CLANG_REV}'\n"
                            "CLANG_SUB_REVISION = 4\n"
                        ).encode()
                    ).decode()
                )
            else:
                self.assertIn(url, downloads)
                requested_downloads.append(url)
                status = downloads[url]
                if status != 200:
                    # ClientSession(raise_for_status=True) raises on entry.
                    response.__aenter__.side_effect = aiohttp.ClientResponseError(
                        request_info=MagicMock(),
                        history=(),
                        status=status,
                        message="Not Found",
                    )

                async def chunks():
                    yield self.ARCHIVE_BODY

                response.content.iter_chunked.return_value = chunks()
            return response

        return ChromiumChecker(mock.Mock(get=get)), requested_downloads

    def assert_archive_update(self, data, url):
        self.assertIsInstance(data.new_version, ExternalFile)
        self.assertEqual(data.new_version.url, url)
        self.assertEqual(data.new_version.version, self.CHROMIUM_VERSION)
        self.assertEqual(data.new_version.size, len(self.ARCHIVE_BODY))
        self.assertEqual(
            data.new_version.checksum,
            MultiDigest(sha256=hashlib.sha256(self.ARCHIVE_BODY).hexdigest()),
        )

    async def test_check_chromium(self):
        data = self.source("chromium")
        checker, downloads = self.checker({self.CHROMIUM_URL: 200})
        await checker.check(data)
        self.assertEqual(downloads, [self.CHROMIUM_URL])
        self.assert_archive_update(data, self.CHROMIUM_URL)

    async def test_check_gentoo_fallback(self):
        data = self.source("chromium")
        checker, downloads = self.checker(
            {self.CHROMIUM_URL: 404, self.GENTOO_URL: 200}
        )
        with self.assertLogs("src.checkers.chromiumchecker", level="ERROR"):
            await checker.check(data)
        self.assertEqual(downloads, [self.CHROMIUM_URL, self.GENTOO_URL])
        self.assert_archive_update(data, self.GENTOO_URL)

    async def test_check_both_mirrors_missing(self):
        data = self.source("chromium")
        checker, downloads = self.checker(
            {self.CHROMIUM_URL: 404, self.GENTOO_URL: 404}
        )
        with (
            self.assertLogs("src.checkers.chromiumchecker", level="ERROR"),
            self.assertRaises(CheckerFetchError) as caught,
        ):
            await checker.check(data)
        self.assertEqual(caught.exception.__cause__.status, 404)
        self.assertEqual(downloads, [self.CHROMIUM_URL, self.GENTOO_URL])
        self.assertIsNone(data.new_version)

    async def test_check_llvm_prebuilt(self):
        data = self.source("llvm-prebuilt")
        checker, downloads = self.checker({self.CLANG_URL: 200})
        await checker.check(data)
        self.assertEqual(downloads, [self.CLANG_URL])
        self.assert_archive_update(data, self.CLANG_URL)

    async def test_check_llvm_git(self):
        data = self.source("llvm-git")
        checker, downloads = self.checker()
        await checker.check(data)
        self.assertEqual(downloads, [])
        self.assertIsInstance(data.new_version, ExternalGitRef)
        self.assertEqual(data.new_version.version, self.CHROMIUM_VERSION)
        self.assertEqual(data.new_version.url, data.current_version.url)
        self.assertEqual(data.new_version.commit, self.CLANG_REV)


class TestLLVMComponent(unittest.IsolatedAsyncioTestCase):
    CHROMIUM_VERSION = "148.0.7778.178"
    CLANG_REV = "llvmorg-23-init-5669-g8a0be0bc"
    CLANG_SUB_REV = "4"

    def make_response(
        self,
        *,
        text=None,
        status_error=None,
    ):
        response = MagicMock()

        response.__aenter__ = AsyncMock(return_value=response)
        response.__aexit__ = AsyncMock(return_value=None)

        if status_error is not None:
            response.raise_for_status.side_effect = status_error
        else:
            response.raise_for_status.return_value = None

        response.text = AsyncMock(return_value=text)

        return response

    async def test_get_llvm_version(self):
        external_data = MagicMock()
        session = MagicMock()

        encoded = base64.b64encode(
            (
                f"CLANG_REVISION = '{self.CLANG_REV}'\n"
                f"CLANG_SUB_REVISION = {self.CLANG_SUB_REV}\n"
            ).encode()
        ).decode()

        response = self.make_response(text=encoded)

        session.get.return_value = response

        component = LLVMGitComponent(
            session,
            external_data,
            self.CHROMIUM_VERSION,
        )

        version = await component.get_llvm_version()

        self.assertEqual(version.revision, self.CLANG_REV)
        self.assertEqual(version.sub_revision, self.CLANG_SUB_REV)

    async def test_llvm_fallback_to_github(self):
        external_data = MagicMock()
        session = MagicMock()

        primary_response = self.make_response(
            status_error=aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=503,
                message="Service Unavailable",
            )
        )

        fallback_response = self.make_response(
            text=(
                f"CLANG_REVISION = '{self.CLANG_REV}'\n"
                f"CLANG_SUB_REVISION = {self.CLANG_SUB_REV}\n"
            )
        )

        session.get.side_effect = [
            primary_response,
            fallback_response,
        ]

        component = LLVMGitComponent(
            session,
            external_data,
            self.CHROMIUM_VERSION,
        )

        version = await component.get_llvm_version()

        self.assertEqual(version.revision, self.CLANG_REV)
        self.assertEqual(version.sub_revision, self.CLANG_SUB_REV)

    async def test_llvm_no_fallback_on_404(self):
        external_data = MagicMock()
        session = MagicMock()

        response = self.make_response(
            status_error=aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=404,
                message="Not Found",
            )
        )

        session.get.return_value = response

        component = LLVMGitComponent(
            session,
            external_data,
            self.CHROMIUM_VERSION,
        )

        with self.assertRaises(aiohttp.ClientResponseError):
            await component.get_llvm_version()


class TestComponentUpdateExternalSource(unittest.IsolatedAsyncioTestCase):
    async def test_update_network_error(self):
        external_data = MagicMock()
        session = MagicMock()

        component = LLVMGitComponent(session, external_data, "148.0.7778.178", None)

        with mock.patch(
            "src.checkers.chromiumchecker.get_extra_data_info_from_url",
            new_callable=AsyncMock,
            side_effect=aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError()
            ),
        ):
            with self.assertRaises(CheckerFetchError):
                await component.update_external_source_version(
                    "https://example.com/file.tar.xz"
                )

    async def test_check_404_fallback(self):
        external_data = MagicMock(spec=ExternalData)
        session = MagicMock()

        component = ChromiumComponent(session, external_data, "126.0.6478.182")

        cause = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=404,
            message="Not Found",
        )
        fetch_error = CheckerFetchError()
        fetch_error.__cause__ = cause

        with mock.patch(
            "src.checkers.chromiumchecker.get_extra_data_info_from_url",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = [
                fetch_error,
                ExternalFile(
                    url="https://chromium-tarballs.distfiles.gentoo.org/chromium-126.0.6478.182-linux.tar.xz",
                    checksum=MultiDigest(sha256="a" * 64),
                    size=100,
                    version="126.0.6478.182",
                    timestamp=None,
                ),
            ]
            await component.check()

        external_data.set_new_version.assert_called_once()

    async def test_check_non_404_reraises_chromium(self):
        external_data = MagicMock(spec=ExternalData)
        session = MagicMock()

        component = ChromiumComponent(session, external_data, "126.0.6478.182")

        cause = aiohttp.ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=503,
            message="Service Unavailable",
        )
        fetch_error = CheckerFetchError()
        fetch_error.__cause__ = cause

        with mock.patch(
            "src.checkers.chromiumchecker.get_extra_data_info_from_url",
            new_callable=AsyncMock,
            side_effect=fetch_error,
        ):
            with self.assertRaises(CheckerFetchError):
                await component.check()

    async def test_check_non_404_reraises(self):
        external_data = MagicMock()
        session = MagicMock()

        component = LLVMGitComponent(session, external_data, "148.0.7778.178")

        with mock.patch(
            "src.checkers.chromiumchecker.get_extra_data_info_from_url",
            new_callable=AsyncMock,
            side_effect=aiohttp.ClientConnectorError(
                connection_key=MagicMock(), os_error=OSError()
            ),
        ):
            with self.assertRaises(CheckerFetchError):
                await component.update_external_source_version(
                    "https://example.com/file.tar.xz"
                )


if __name__ == "__main__":
    unittest.main()
