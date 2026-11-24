import gzip
import os
import unittest
from datetime import datetime, timezone
from unittest import mock

from src.checkers.rpmrepochecker import RPMRepoChecker
from src.lib.checksums import MultiDigest
from src.lib.externaldata import ExternalData, ExternalFile
from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from tests.support import LocalHTTPTestCase, upstream_test

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.visualstudio.code.yaml")


class TestRPMRepoChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    @upstream_test
    async def test_check(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()
        for data in ext_data:
            self.assertIsNotNone(data)
            self.assertIsNotNone(data.new_version)
            self.assertIsNotNone(data.new_version.url)
            self.assertIsNotNone(data.new_version.checksum)
            self.assertIsNotNone(data.new_version.version)
            self.assertNotEqual(data.new_version.url, data.current_version.url)
            self.assertIsInstance(data.new_version.checksum, MultiDigest)
            self.assertNotEqual(
                data.new_version.checksum, data.current_version.checksum
            )
            self.assertRegex(
                data.new_version.url,
                rf"https://packages\.microsoft\.com/yumrepos/.+?/code-.+\.{data.arches[0]}\.rpm",
            )


class TestRPMRepoMetadata(LocalHTTPTestCase):
    async def test_selects_latest_package_for_source_architecture(self):
        root = str(self.server.make_url("/repo/"))
        self.responses["/repo/repodata/repomd.xml"] = {
            "body": b"""\
<repomd xmlns="http://linux.duke.edu/metadata/repo">
  <data type="primary"><location href="repodata/primary.xml.gz"/></data>
</repomd>""",
        }
        packages = [
            ("app", "x86_64", "1.9"),
            ("app", "x86_64", "1.10"),
            ("app", "aarch64", "9.0"),
            ("other", "x86_64", "9.0"),
        ]
        checksum = "a" * 64
        entries = "".join(
            f"""\
<package type="rpm">
  <name>{name}</name><arch>{arch}</arch><version ver="{version}"/>
  <checksum type="sha256">{checksum}</checksum>
  <location href="Packages/{name}-{version}.{arch}.rpm"/>
  <size archive="42"/><time file="1704067200"/>
</package>"""
            for name, arch, version in packages
        )
        self.responses["/repo/repodata/primary.xml.gz"] = {
            "body": gzip.compress(
                f'<metadata xmlns="http://linux.duke.edu/metadata/common">'
                f"{entries}</metadata>".encode(),
                mtime=0,
            ),
        }
        data = ExternalData.from_source(
            "sources.json",
            {
                "type": "file",
                "url": root + "Packages/app-1.9.x86_64.rpm",
                "sha256": "0" * 64,
                "only-arches": ["x86_64"],
                "x-checker-data": {
                    "type": "rpm-repo",
                    "root": root.rstrip("/"),
                    "package-name": "app",
                },
            },
        )

        await RPMRepoChecker(self.http).check(data)

        self.assertEqual(
            data.new_version,
            ExternalFile(
                url=root + "Packages/app-1.10.x86_64.rpm",
                checksum=MultiDigest(sha256=checksum),
                size=42,
                version="1.10",
                timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            ),
        )
        self.assertEqual(
            self.requests,
            [
                ("GET", "/repo/repodata/repomd.xml"),
                ("GET", "/repo/repodata/primary.xml.gz"),
            ],
        )
