import unittest
from unittest import mock

from src.checkers.electronchecker import ElectronChecker
from src.lib.externaldata import ExternalBase
from src.lib.sourceupdates import SourceUpdate


class TestElectronSourceUpdates(unittest.IsolatedAsyncioTestCase):
    async def test_previous_version_from_metadata_filename(self):
        for arch, metadata in (
            ("x86_64", "latest-linux.yml"),
            ("arm64", "latest-linux-arm64.yml"),
        ):
            with self.subTest(arch=arch):
                base = "https://cdn.squidservers.com/"
                source = {
                    "type": "file",
                    "url": f"{base}squidservers-0.10.2-{arch}.AppImage",
                    "sha512": "abcd",
                    "x-checker-data": {
                        "type": "electron-updater",
                        "url": base + metadata,
                    },
                }
                data = ExternalBase.from_source("manifest.yml", source)
                current = data.current_version
                response = mock.AsyncMock()
                response.__aenter__.return_value = response
                response.read.return_value = f"""
version: 0.10.3
files:
  - url: squidservers-0.10.3-{arch}.AppImage
    size: 123
    sha512: dGVzdA==
releaseDate: '2026-01-01T00:00:00Z'
""".encode()
                session = mock.MagicMock()
                session.get.return_value = response
                await ElectronChecker(session).check(data)
                update = SourceUpdate.from_source(data)
                self.assertEqual(update.old_version, "0.10.2")
                self.assertEqual(update.new_version, "0.10.3")
                self.assertEqual(data.current_version, current)
                self.assertEqual(source["url"], current.url)
                session.get.assert_called_once()

    def test_partial_version_is_not_inferred(self):
        for old_name, new_name in (
            ("app-10.10.2.AppImage", "app-10.10.3.AppImage"),
            ("app-0.10.2.4.AppImage", "app-0.10.3.4.AppImage"),
            ("app-build0.10.2.AppImage", "app-build0.10.3.AppImage"),
        ):
            with self.subTest(new_name=new_name):
                data = self.make_source(old_name, new_name)
                self.assertEqual(SourceUpdate.from_source(data).old_version, "unknown")

    def make_source(self, old_name, new_name, version="0.10.3"):
        data = ExternalBase.from_source(
            "manifest.yml",
            {
                "type": "file",
                "url": "https://example.org/" + old_name,
                "sha512": "abcd",
                "x-checker-data": {"type": "electron-updater"},
            },
        )
        data.new_version = data.current_version._replace(
            url="https://example.org/" + new_name, version=version
        )
        return data

    def test_matching_version_shapes(self):
        for old, new, template in (
            ("0.10.2", "0.10.3", "app-v{}.AppImage"),
            ("0.10.2-beta.1", "0.10.3-beta.2", "app-{}-arm64.AppImage"),
            ("0.10.2+build.1", "0.10.3+build.2", "app-{}.AppImage"),
            ("0.10.2", "0.10.3", "{}"),
            ("0.10.3", "0.10.3", "app-{}.AppImage"),
        ):
            with self.subTest(old=old, new=new, template=template):
                data = self.make_source(template.format(old), template.format(new), new)
                self.assertEqual(SourceUpdate.from_source(data).old_version, old)

    def test_ambiguous_or_mismatched_urls(self):
        for old, new, version in (
            ("app-latest.AppImage", "app-latest.AppImage", "0.10.3"),
            ("app-0.10.2.AppImage", "app-0.10.3.AppImage", None),
            ("app-1.2.AppImage", "app-1.3.AppImage", "1.3"),
            ("app-0.10.2.AppImage", "app-0.10.3.AppImage", "invalid"),
            ("app-0.10.2-0.10.2.AppImage", "app-0.10.3-0.10.3.AppImage", "0.10.3"),
            ("app-0.10.2-arm64.AppImage", "app-0.10.3-x86_64.AppImage", "0.10.3"),
            ("old/app-0.10.2.AppImage", "new/app-0.10.3.AppImage", "0.10.3"),
            ("0.10.2/app.AppImage", "0.10.3/app.AppImage", "0.10.3"),
            ("app-0.10.2.AppImage?a=1", "app-0.10.3.AppImage?a=2", "0.10.3"),
            ("app-0.10.2.AppImage#old", "app-0.10.3.AppImage#new", "0.10.3"),
            ("app-invalid.AppImage", "app-0.10.3.AppImage", "0.10.3"),
        ):
            with self.subTest(old=old, new=new, version=version):
                data = self.make_source(old, new, version)
                self.assertEqual(SourceUpdate.from_source(data).old_version, "unknown")

    def test_origin_and_checker_scope(self):
        for url in (
            "https://other.org/app-0.10.2.AppImage",
            "http://example.org/app-0.10.2.AppImage",
            "https://[invalid",
        ):
            with self.subTest(url=url):
                data = self.make_source("app-0.10.2.AppImage", "app-0.10.3.AppImage")
                data.current_version = data.current_version._replace(url=url)
                self.assertEqual(SourceUpdate.from_source(data).old_version, "unknown")
        data = self.make_source("app-0.10.2.AppImage", "app-0.10.3.AppImage")
        data.checker_data["type"] = "json"
        self.assertEqual(SourceUpdate.from_source(data).old_version, "unknown")

    def test_explicit_version_and_release_tag_fallback(self):
        data = self.make_source("app-0.10.2.AppImage", "app-0.10.3.AppImage")
        data.current_version = data.current_version._replace(version="explicit")
        self.assertEqual(SourceUpdate.from_source(data).old_version, "explicit")
        data.current_version = data.current_version._replace(
            version=None,
            url="https://github.com/owner/app/releases/download/v0.10.2/app.AppImage",
        )
        self.assertEqual(SourceUpdate.from_source(data).old_version, "tag v0.10.2")
