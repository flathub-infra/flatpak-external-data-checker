import logging
import os
import unittest
from distutils.version import LooseVersion

from src.checker import ManifestChecker
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
                LooseVersion(data.new_version.version), LooseVersion("90.0.4430.212")
            )

            if isinstance(data, ExternalData):
                self.assertIsInstance(data.new_version, ExternalFile)
                self.assertIsNotNone(data.new_version.checksum)
                self.assertIsInstance(data.new_version.checksum, MultiDigest)
                if data.filename.startswith("chromium-"):
                    self.assertRegex(
                        data.new_version.url,
                        r"^https://commondatastorage.googleapis.com/chromium-browser-official/chromium-[\d.]+\.tar\.xz$",
                    )
                    self.assertNotEqual(
                        data.new_version.checksum,
                        MultiDigest(
                            sha256="abe11d0cb1ff21278aad2eec1a1e279d59176b15331804d7df1807446786d59e"
                        ),
                    )
                elif data.filename.startswith("clang-"):
                    self.assertRegex(
                        data.new_version.url,
                        r"^https://commondatastorage.googleapis.com/chromium-browser-clang/Linux_x64/clang-.*\.tgz$",
                    )
                    self.assertNotEqual(
                        data.new_version.checksum,
                        MultiDigest(
                            sha256="676448e180fb060d3983f24476a2136eac83c6011c600117686035634a2bbe26"
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
