import unittest
from datetime import datetime
from unittest import mock

from src.checkers.rustchecker import RustChecker
from src.lib.checksums import MultiDigest
from src.lib.externaldata import ExternalData, ExternalFile
from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from tests.support import upstream_test, write_sources


class TestRustChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    @upstream_test
    async def test_check(self):
        checker = ManifestChecker(
            write_sources(
                self,
                {
                    "type": "archive",
                    "url": "https://example.invalid/rust-old.tar.gz",
                    "sha256": "0" * 64,
                    "x-checker-data": {
                        "type": "rust",
                        "package": "rust",
                        "channel": "nightly",
                        "target": "x86_64-unknown-linux-gnu",
                    },
                },
            )
        )
        ext_data = await checker.check()

        self.assertEqual(len(ext_data), 1)
        data = ext_data[0]
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https://static.rust-lang.org/dist/[\-\d]+/rust-nightly-x86_64-unknown-linux-gnu.tar.xz$",
        )
        self.assertIsNone(data.new_version.size)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, MultiDigest)
        self.assertNotEqual(data.new_version.checksum, data.current_version.checksum)


class TestRustChannelMetadata(unittest.IsolatedAsyncioTestCase):
    async def test_nightly_version_includes_release_date(self):
        await self.check_channel("nightly", "1.84.0-nightly-20241101")

    async def test_default_channel_is_stable(self):
        await self.check_channel(None, "1.84.0")

    async def test_unavailable_target_has_no_update(self):
        await self.check_channel("nightly", None, available=False)

    async def check_channel(self, channel, expected_version, *, available=True):
        target = "x86_64-unknown-linux-gnu"
        metadata = {"type": "rust", "package": "rust", "target": target}
        if channel is not None:
            metadata["channel"] = channel
        data = ExternalData.from_source(
            "sources.json",
            {
                "type": "archive",
                "url": "https://example.invalid/old.tar.xz",
                "sha256": "0" * 64,
                "x-checker-data": metadata,
            },
        )
        channel = channel or "stable"
        url = f"https://static.rust-lang.org/dist/2024-11-01/rust-{channel}-{target}.tar.xz"
        checksum = "a" * 64
        version = "1.84.0-nightly" if channel == "nightly" else "1.84.0"
        body = f"""\
date = "2024-11-01"
[pkg.rust]
version = "{version} (abcdef123 2024-10-31)"
[pkg.rust.target.{target}]
"""
        if available:
            body += f'available = true\nxz_url = "{url}"\nxz_hash = "{checksum}"\n'
        else:
            body += "available = false\n"
        response = mock.Mock(text=mock.AsyncMock(return_value=body))
        request = mock.MagicMock()
        request.__aenter__.return_value = response
        session = mock.Mock(get=mock.Mock(return_value=request))

        await RustChecker(session).check(data)

        session.get.assert_called_once_with(
            f"https://static.rust-lang.org/dist/channel-rust-{channel}.toml"
        )
        if expected_version is None:
            self.assertIsNone(data.new_version)
        else:
            self.assertEqual(
                data.new_version,
                ExternalFile(
                    url=url,
                    checksum=MultiDigest(sha256=checksum),
                    size=None,
                    version=expected_version,
                    timestamp=datetime.fromisoformat("2024-11-01"),
                ),
            )


if __name__ == "__main__":
    unittest.main()
