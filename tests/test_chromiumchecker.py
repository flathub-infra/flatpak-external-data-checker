import logging
import os
import unittest
from distutils.version import LooseVersion

from src.manifest import ManifestChecker
from src.lib.externaldata import (
    ExternalData,
    ExternalFile,
    ExternalGitRef,
    ExternalGitRepo,
)
from src.lib.checksums import MultiDigest
from src.lib.utils import init_logging

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
                        r"^https://commondatastorage.googleapis.com/chromium-browser-official/chromium-[\d.]+\.tar\.xz$",  # noqa: E501
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
                        r"^https://commondatastorage.googleapis.com/chromium-browser-clang/Linux_x64/clang-.*\.tgz$",  # noqa: E501
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
