import os
import unittest
from unittest import mock

from src.checkers.jetbrainschecker import JetBrainsChecker
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerMetadataError, CheckerQueryError
from src.lib.externaldata import ExternalBase
from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from tests.support import upstream_test

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.jetbrains.PhpStorm.json")


class TestJetBrainsChecker(unittest.IsolatedAsyncioTestCase):
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

        data = self._find_by_filename(ext_data, "phpstorm.tar.gz")
        self.assertIsNotNone(data)
        self.assertEqual(data.filename, "phpstorm.tar.gz")
        self.assertIsNotNone(data.new_version)
        self.assertRegex(
            data.new_version.url,
            r"^https://download\.jetbrains\.com/webide/PhpStorm-.+\.tar\.gz$",
        )
        self.assertIsInstance(data.new_version.size, int)
        self.assertGreater(data.new_version.size, 0)
        self.assertIsNotNone(data.new_version.checksum)
        self.assertIsInstance(data.new_version.checksum, MultiDigest)
        self.assertNotEqual(
            data.new_version.checksum,
            MultiDigest(
                sha256="0000000000000000000000000000000000000000000000000000000000000000"
            ),
        )

    def _find_by_filename(self, ext_data, filename):
        for data in ext_data:
            if data.filename == filename:
                return data
        return None


class TestJetBrainsValidation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.session = mock.MagicMock()
        self.checker = JetBrainsChecker(self.session)

    def source(self, **metadata):
        return ExternalBase.from_source(
            "app.json",
            {
                "type": "archive",
                "url": "https://example.org/app-1.tar.gz",
                "sha256": "old",
                "x-checker-data": {"type": "jetbrains", **metadata},
            },
        )

    async def test_supported_release_types(self):
        for release_type in ("release", "eap", "rc", "preview"):
            with self.subTest(release_type=release_type):
                await self.checker.validate_checker_data(
                    self.source(code="IIU", **{"release-type": release_type})
                )
        self.session.get.assert_not_called()

    async def test_release_type_is_optional(self):
        await self.checker.validate_checker_data(self.source(code="IIU"))

    async def test_invalid_release_types(self):
        for release_type in (
            "stable",
            "beta",
            "EAP",
            "",
            "release,eap",
            None,
            1,
            True,
            [],
            {},
        ):
            with self.subTest(release_type=release_type):
                with self.assertRaises(CheckerMetadataError) as raised:
                    await self.checker.validate_checker_data(
                        self.source(code="IIU", **{"release-type": release_type})
                    )
                self.assertEqual(
                    list(raised.exception.__cause__.path), ["release-type"]
                )
        self.session.get.assert_not_called()

    async def test_code_is_required(self):
        with self.assertRaises(CheckerMetadataError):
            await self.checker.validate_checker_data(
                self.source(**{"release-type": "release"})
            )


class TestJetBrainsReleaseLinks(unittest.IsolatedAsyncioTestCase):
    async def test_release_notes_from_selected_release(self):
        for notes in ("https://example.org/notes/2", None, "javascript:alert(1)", 123):
            with self.subTest(notes=notes):
                source = ExternalBase.from_source(
                    "app.json",
                    {
                        "type": "archive",
                        "url": "https://example.org/app-1.tar.gz",
                        "sha256": "old",
                        "x-checker-data": {"type": "jetbrains", "code": "IIU"},
                    },
                )
                release = {
                    "version": "2",
                    "date": "2026-01-01",
                    "notesLink": notes,
                    "downloads": {
                        "linux": {
                            "link": "https://example.org/app-2.tar.gz",
                            "size": 42,
                            "checksumLink": "https://example.org/app-2.sha256",
                        }
                    },
                }
                metadata = mock.AsyncMock()
                metadata.json.return_value = {"IIU": [release]}
                checksum = mock.AsyncMock()
                checksum.text.return_value = "abc  app-2.tar.gz"
                checker = JetBrainsChecker.__new__(JetBrainsChecker)
                checker.robots_cache = None
                checker.session = mock.MagicMock()
                checker.session.get.return_value.__aenter__ = mock.AsyncMock(
                    side_effect=[metadata, checksum]
                )
                await checker.check(source)
                self.assertEqual(source.new_version.version, "2")
                expected = notes if notes == "https://example.org/notes/2" else None
                self.assertEqual(source.new_release_url, expected)
                self.assertEqual(checker.session.get.call_count, 2)
                self.assertEqual(
                    checker.session.get.call_args_list[0],
                    mock.call(
                        "https://data.services.jetbrains.com/products/releases",
                        params={"code": "IIU", "latest": "true", "type": "release"},
                    ),
                )


class TestJetBrainsOSS(unittest.IsolatedAsyncioTestCase):
    def source(self, code="IIC", arch="x86_64", **metadata):
        return ExternalBase.from_source(
            "app.json",
            {
                "type": "archive",
                "url": "https://example.org/old.tar.gz",
                "sha256": "old",
                "only-arches": [arch],
                "x-checker-data": {
                    "type": "jetbrains",
                    "code": code,
                    "oss": True,
                    **metadata,
                },
            },
        )

    def release(self, product="idea", version="2026.3", **overrides):
        tag = f"{product}/{version}"
        return {
            "tag_name": tag,
            "draft": False,
            "prerelease": False,
            "published_at": "2026-11-01T12:00:00Z",
            "html_url": f"https://github.com/JetBrains/intellij-community/releases/tag/{tag}",
            "assets": [
                {
                    "name": f"{product}-{version}{suffix}.tar.gz",
                    "state": "uploaded",
                    "browser_download_url": (
                        "https://github.com/JetBrains/intellij-community/releases/"
                        f"download/{tag}/{product}-{version}{suffix}.tar.gz"
                    ),
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                }
                for suffix in ("", "-aarch64")
            ],
            **overrides,
        }

    def checker(self, releases):
        checker = JetBrainsChecker(mock.MagicMock())
        checker._get_json = mock.AsyncMock(return_value=releases)
        return checker

    async def test_metadata(self):
        checker = self.checker([])
        for code in ("IIC", "PCC"):
            await checker.validate_checker_data(self.source(code))
        for metadata in (
            {"code": "IIU"},
            {"release-type": "rc"},
            {"release-type": "eap"},
            {"release-type": "preview"},
            {"oss": "true"},
        ):
            with self.subTest(metadata=metadata):
                with self.assertRaises(CheckerMetadataError):
                    await checker.validate_checker_data(self.source(**metadata))

    async def test_numeric_stable_selection_and_exact_pairing(self):
        for code, product in (("IIC", "idea"), ("PCC", "pycharm")):
            for arch, index in (("x86_64", 0), ("aarch64", 1)):
                with self.subTest(code=code, arch=arch):
                    selected = self.release(product, "2026.10.0.10")
                    releases = [
                        self.release(product, "2025.3.6.3"),
                        self.release(
                            product, "2026.11", tag_name=f"{product}/2026.11-rc-2"
                        ),
                        self.release(product, "2026.12", prerelease=True),
                        self.release(product, "2026.13", draft=True),
                        self.release(
                            "pycharm" if product == "idea" else "idea", "2027.1"
                        ),
                        self.release(product, "2026.9"),
                        self.release(product, "2026.10.0.9"),
                        selected,
                    ]
                    checker = self.checker(releases)
                    source = self.source(code, arch)
                    await checker.check(source)
                    self.assertEqual(source.new_version.version, "2026.10.0.10")
                    self.assertEqual(
                        source.new_version.url,
                        selected["assets"][index]["browser_download_url"],
                    )
                    self.assertEqual(source.new_version.size, 123)
                    self.assertEqual(
                        source.new_version.checksum, MultiDigest(sha256="a" * 64)
                    )
                    self.assertEqual(
                        source.new_version.timestamp.isoformat(),
                        "2026-11-01T12:00:00+00:00",
                    )
                    self.assertEqual(source.new_release_url, selected["html_url"])
                    checker.session.get.assert_not_called()

    async def test_all_pages_are_considered(self):
        checker = self.checker([])
        checker._get_json.side_effect = [
            [self.release(version="2025.3")] * 100,
            [self.release(version="2026.3")],
        ]
        source = self.source()
        await checker.check(source)
        self.assertEqual(source.new_version.version, "2026.3")
        self.assertTrue(checker._get_json.call_args.args[0].endswith("page=2"))

    async def test_missing_or_mismatched_assets_do_not_fall_back(self):
        for release in (
            self.release(assets=[]),
            self.release(assets=self.release()["assets"][:1]),
            self.release(version="2026.3.0.1", tag_name="idea/2026.3"),
            self.release(assets=self.release()["assets"] * 2),
        ):
            with self.subTest(release=release):
                checker = self.checker([release, self.release(version="2025.3")])
                with self.assertRaises(CheckerQueryError):
                    await checker.check(self.source(arch="aarch64"))

    async def test_no_stable_release(self):
        for releases in (
            [],
            [self.release(tag_name="idea/2026.3-rc")],
            [self.release(product="pycharm")],
            {"message": "API error"},
        ):
            with self.subTest(releases=releases):
                with self.assertRaises(CheckerQueryError):
                    await self.checker(releases).check(self.source())

    async def test_missing_digest_uses_download_checksum(self):
        release = self.release()
        release["assets"][0]["digest"] = None
        checker = self.checker([release])
        checker._update_version = mock.AsyncMock()
        source = self.source()
        await checker.check(source)
        checker._update_version.assert_awaited_once_with(
            source,
            "2026.3",
            release["assets"][0]["browser_download_url"],
            timestamp=mock.ANY,
        )

    async def test_sha512_source_completes_missing_digest(self):
        release = self.release()
        checker = self.checker([release])
        source = ExternalBase.from_source(
            "app.json",
            {
                "type": "archive",
                "url": "https://example.org/old.tar.gz",
                "sha512": "b" * 128,
                "x-checker-data": {"type": "jetbrains", "code": "IIC", "oss": True},
            },
        )
        completed = MultiDigest(sha256="a" * 64, sha512="c" * 128)
        checker._complete_digests = mock.AsyncMock(return_value=completed)
        await checker.check(source)
        url = release["assets"][0]["browser_download_url"]
        checker._complete_digests.assert_awaited_once_with(
            url, MultiDigest(sha256="a" * 64), mirror_urls=[]
        )
        self.assertEqual(source.new_version.checksum, completed)
        self.assertEqual(source.new_version.version, "2026.3")
        self.assertEqual(source.new_version.url, url)

    async def test_legacy_api_for_both_architectures_and_release_types(self):
        for arch, platform in (("x86_64", "linux"), ("aarch64", "linuxARM64")):
            for release_type in ("release", "eap", "rc", "preview"):
                with self.subTest(arch=arch, release_type=release_type):
                    source = self.source(
                        arch=arch, oss=False, **{"release-type": release_type}
                    )
                    response = mock.AsyncMock()
                    response.json.return_value = {
                        "IIC": [
                            {
                                "version": "2025.3",
                                "date": "2025-12-01",
                                "downloads": {
                                    platform: {
                                        "link": "https://download.jetbrains.com/idea/app.tar.gz",
                                        "size": 123,
                                        "checksumLink": "https://example.org/checksum",
                                    }
                                },
                            }
                        ]
                    }
                    checksum = mock.AsyncMock()
                    checksum.text.return_value = "a" * 64 + "  app.tar.gz"
                    checker = self.checker([])
                    checker.session.get.return_value.__aenter__ = mock.AsyncMock(
                        side_effect=[response, checksum]
                    )
                    await checker.check(source)
                    checker._get_json.assert_not_called()
                    self.assertEqual(source.new_version.version, "2025.3")
                    self.assertEqual(
                        checker.session.get.call_args_list[0],
                        mock.call(
                            "https://data.services.jetbrains.com/products/releases",
                            params={
                                "code": "IIC",
                                "latest": "true",
                                "type": release_type,
                            },
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
