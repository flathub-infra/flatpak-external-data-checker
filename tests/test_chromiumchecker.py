import base64
import logging
import os
import unittest
from unittest.mock import AsyncMock, MagicMock

import aiohttp

from src.manifest import ManifestChecker
from src.checkers.chromiumchecker import LLVMGitComponent
from src.lib.externaldata import (
    ExternalData,
    ExternalFile,
    ExternalGitRef,
    ExternalGitRepo,
)
from src.lib.checksums import MultiDigest
from src.lib.utils import init_logging
from src.lib.version import LooseVersion

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "org.chromium.Chromium.yaml")


class TestChromiumChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging(logging.INFO)

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
                        r"^https://(commondatastorage.googleapis.com/chromium-browser-official|chromium-tarballs.distfiles.gentoo.org)/chromium-[\d.]+\.tar\.xz$",  # noqa: E501
                    )
                    self.assertNotEqual(
                        data.new_version.checksum,
                        MultiDigest(
                            sha256="a68d31f77a6b7700a5161d82f5932c2822f85f7ae68ad51be3d3cf689a3fe2b0"  # noqa: E501
                        ),
                    )
                elif data.filename.startswith("clang-"):
                    self.assertRegex(
                        data.new_version.url,
                        r"^https://commondatastorage.googleapis.com/chromium-browser-clang/Linux_x64/clang-.*\.tar\.xz$",  # noqa: E501
                    )
                    self.assertNotEqual(
                        data.new_version.checksum,
                        MultiDigest(
                            sha256="cf6b516a4e410d79439a150927fc8b450b325e2a6349395ae153c9d2dd6c6ed2"  # noqa: E501
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
