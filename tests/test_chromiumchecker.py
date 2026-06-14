import base64
import logging
import os
import unittest
from unittest import mock
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from src.checkers.chromiumchecker import ChromiumComponent, LLVMGitComponent
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerFetchError
from src.lib.externaldata import (
    ExternalData,
    ExternalFile,
    ExternalGitRef,
    ExternalGitRepo,
)
from src.lib.utils import init_logging
from src.lib.version import LooseVersion
from src.manifest import ManifestChecker

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.chromium.Chromium.yaml")


class TestChromiumChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging(logging.INFO)
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        self.assertEqual(len(ext_data), 3)
        for data in ext_data:
            self.assertIsNotNone(data.new_version)
            self.assertIsNotNone(data.new_version.version)
            self.assertGreater(
                LooseVersion(data.new_version.version), LooseVersion("100.0.4845.0")
            )

            if isinstance(data, ExternalData):
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                if data.filename.startswith("chromium-"):
                    self.assertRegex(
                        data.new_version.url,
                        r"^https://(commondatastorage.googleapis.com/chromium-browser-official|chromium-tarballs.distfiles.gentoo.org)/chromium-[\d.]+\.tar\.xz$",
                    )
                    self.assertNotEqual(
                        data.new_version.checksum,
                        MultiDigest(
                            sha256="a68d31f77a6b7700a5161d82f5932c2822f85f7ae68ad51be3d3cf689a3fe2b0"
                        ),
                    )
                elif data.filename.startswith("clang-"):
                    self.assertRegex(
                        data.new_version.url,
                        r"^https://commondatastorage.googleapis.com/chromium-browser-clang/Linux_x64/clang-.*\.tar\.xz$",
                    )
                    self.assertNotEqual(
                        data.new_version.checksum,
                        MultiDigest(
                            sha256="cf6b516a4e410d79439a150927fc8b450b325e2a6349395ae153c9d2dd6c6ed2"
                        ),
                    )
                else:
                    self.fail(f"unexpected extra-data filename {data.filename}")
            elif isinstance(data, ExternalGitRepo):
                self.assertEqual(data.filename, "llvm-project")
                self.assertIsInstance(data.new_version, ExternalGitRef)
                self.assertIsNotNone(data.new_version.commit)
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
            else:
                self.fail(repr(type(data)))


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
