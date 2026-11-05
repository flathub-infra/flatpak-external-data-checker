import copy
import unittest
from unittest import mock

from src.checkers import ALL_CHECKERS
from src.checkers.forgejochecker import ForgejoChecker
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerMetadataError, CheckerQueryError
from src.lib.externaldata import ExternalBase, ExternalFile
from src.lib.sourceupdates import SourceUpdate
from src.manifest import ManifestChecker
from tests.support import TAG_PATTERN_CASES


class TestForgejoChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.checker = ForgejoChecker(mock.MagicMock())
        self.repo = "example/app"
        self.instance = "https://codeberg.org"
        self.release = {
            "tag_name": "v1.2.3",
            "draft": False,
            "prerelease": False,
            "published_at": "2026-11-04T12:00:00Z",
            "html_url": "https://codeberg.org/example/app/releases/tag/v1.2.3",
            "tarball_url": "https://codeberg.org/example/app/archive/v1.2.3.tar.gz",
            "zipball_url": "https://codeberg.org/example/app/archive/v1.2.3.zip",
            "assets": [
                {
                    "name": "app-v1.2.3-linux.tar.xz",
                    "browser_download_url": "https://codeberg.org/attachments/example",
                }
            ],
        }
        self.fetch = mock.AsyncMock(return_value=self.release)
        self.checker._get_json = self.fetch
        self.download = mock.AsyncMock(side_effect=self.download_file)
        patcher = mock.patch(
            "src.checkers.utils.get_extra_data_info_from_url", self.download
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    async def download_file(*, url, **kwargs):
        return ExternalFile(
            url=url,
            version=None,
            timestamp=None,
            checksum=MultiDigest(sha256="b" * 64),
            size=123,
        )

    def source(self, metadata=None, *, git=False, url=None):
        return ExternalBase.from_source(
            "test.json",
            {
                "type": "git" if git else "archive",
                "url": url
                or (
                    f"{self.instance}/{self.repo}.git"
                    if git
                    else "https://example.org/old.tar.gz"
                ),
                **({"tag": "v1", "commit": "a" * 40} if git else {"sha256": "a" * 64}),
                "x-checker-data": {
                    "type": "forgejo",
                    "instance": self.instance,
                    "repo": self.repo,
                    **(metadata or {}),
                },
            },
        )

    async def test_asset_without_github_state_and_metainfo(self):
        source = self.source(
            {"asset-name": "app-${tag}-linux.tar.xz", "tag-pattern": r"v(.+)"}
        )
        await self.checker.validate_checker_data(source)
        await self.checker.check(source)
        self.assertIn(ForgejoChecker, ALL_CHECKERS)
        self.fetch.assert_awaited_once_with(
            "https://codeberg.org/api/v1/repos/example/app/releases/latest"
        )
        self.assertEqual(source.new_version.version, "1.2.3")
        self.assertEqual(
            source.new_version.url, self.release["assets"][0]["browser_download_url"]
        )
        self.assertEqual(source.new_version.checksum.sha256, "b" * 64)
        self.assertEqual(
            source.new_version.timestamp.isoformat(), "2026-11-04T12:00:00+00:00"
        )
        self.assertEqual(
            SourceUpdate.from_source(source).release_url, self.release["html_url"]
        )
        manifest = mock.Mock(app_id="org.example.App", _root_manifest_path="test.json")
        manifest.get_external_data.return_value = [source]
        manifest._main_sources.side_effect = lambda: ManifestChecker._main_sources(
            manifest
        )
        with (
            mock.patch("src.manifest.find_appdata_file", return_value="metainfo.xml"),
            mock.patch("src.manifest.add_release_to_file") as add_release,
        ):
            ManifestChecker._update_appdata(manifest)
        self.assertEqual(
            add_release.call_args.kwargs["release_url"], self.release["html_url"]
        )

    async def test_archives_on_self_hosted_subpath(self):
        self.instance = "https://forge.example:8443/code/"
        base = "https://forge.example:8443/code"
        self.release["html_url"] = f"{base}/{self.repo}/releases/tag/v1.2.3"
        for kind in ("tarball", "zipball"):
            with self.subTest(kind=kind):
                source = self.source({"source-archive": kind})
                await self.checker.validate_checker_data(source)
                await self.checker.check(source)
                self.assertEqual(
                    self.fetch.await_args.args[0],
                    f"{base}/api/v1/repos/{self.repo}/releases/latest",
                )
                self.assertEqual(source.new_version.url, self.release[f"{kind}_url"])
                self.assertEqual(source.new_version.version, "v1.2.3")
                self.assertEqual(source.new_release_url, self.release["html_url"])

    async def test_git_release_tag(self):
        source = self.source(git=True)
        with mock.patch(
            "src.lib.utils.git_ls_remote",
            return_value={
                "refs/tags/v1.2.3": "tag-object",
                "refs/tags/v1.2.3^{}": "b" * 40,
            },
        ):
            await self.checker.validate_checker_data(source)
            await self.checker.check(source)
        self.assertEqual(source.new_version.tag, "v1.2.3")
        self.assertEqual(source.new_version.commit, "b" * 40)
        self.assertEqual(source.new_release_url, self.release["html_url"])
        self.download.assert_not_awaited()

    async def test_git_mismatch(self):
        for url in (
            "https://other.org/example/app",
            "https://codeberg.org/other/app",
            "https://codeberg.org/example/app?x=1",
        ):
            with self.subTest(url=url), self.assertRaises(CheckerMetadataError):
                await self.checker.check(self.source(git=True, url=url))
        self.fetch.assert_not_awaited()

    async def test_repository_casing_preserves_tag_and_deployment_path(self):
        self.instance = "https://forge.example/Code"
        tag = "V1.2.3"
        page = f"{self.instance}/Example/App/releases/tag/{tag}"
        self.release.update(tag_name=tag, html_url=page)
        source = self.source({"source-archive": "tarball"})
        await self.checker.check(source)
        self.assertEqual(source.new_release_url, page)
        self.assertEqual(source.new_version.version, tag)

        source = self.source(git=True, url=f"{self.instance}/Example/App.git")
        with mock.patch(
            "src.lib.utils.git_ls_remote",
            return_value={f"refs/tags/{tag}": "b" * 40},
        ):
            await self.checker.check(source)
        self.assertEqual(source.new_version.tag, tag)
        self.assertEqual(source.new_release_url, page)

        for path in ("/code/Example/App", "/Code/Other/App", "/Code/"):
            with self.subTest(path=path):
                source = self.source(git=True, url=f"https://forge.example{path}")
                with self.assertRaises(CheckerMetadataError):
                    await self.checker.check(source)
                self.release["html_url"] = (
                    f"https://forge.example{path}/releases/tag/{tag}"
                )
                source = self.source({"source-archive": "tarball"})
                await self.checker.check(source)
                self.assertIsNone(source.new_release_url)

    async def test_invalid_metadata(self):
        for metadata in (
            {},
            {"source-archive": "tar"},
            {"source-archive": "tarball", "asset-name": "app"},
            {"source-archive": "tarball", "versions": {"<": "2"}},
            {"source-archive": "tarball", "repo": "owner/repo/extra"},
        ):
            with (
                self.subTest(metadata=metadata),
                self.assertRaises(CheckerMetadataError),
            ):
                await self.checker.validate_checker_data(self.source(metadata))
        for instance in (
            "file:///tmp/forge",
            "https:///forge",
            "https://user:pass@forge.org",
            "https://forge.org?x=1",
            "https://forge.org/#frag",
            "https://forge.org:bad",
            "https://forge.org/white space",
            "https://forge.org\\path",
        ):
            with (
                self.subTest(instance=instance),
                self.assertRaises(CheckerMetadataError),
            ):
                await self.checker.validate_checker_data(
                    self.source({"source-archive": "tarball", "instance": instance})
                )
        with self.assertRaises(CheckerMetadataError):
            await self.checker.validate_checker_data(
                self.source({"source-archive": "tarball"}, git=True)
            )

    async def test_bad_release_or_asset(self):
        for change in (
            {"draft": True},
            {"prerelease": True},
            {"tag_name": None},
            {"published_at": "bad"},
            {"published_at": 123},
            {"assets": None},
            {"assets": []},
            {"assets": self.release["assets"] * 2},
            {
                "assets": [
                    {
                        "name": "app-v1.2.3-linux.tar.xz",
                        "browser_download_url": "file:///bad",
                    }
                ]
            },
        ):
            with self.subTest(change=change):
                self.fetch.return_value = self.release | change
                source = self.source({"asset-name": "app-${tag}-linux.tar.xz"})
                with self.assertRaises(CheckerQueryError):
                    await self.checker.check(source)
                self.assertIsNone(source.new_version)
        self.download.assert_not_awaited()

    async def test_tag_pattern_extraction(self):
        for pattern, tag, version in TAG_PATTERN_CASES:
            with self.subTest(pattern=pattern, tag=tag):
                self.release["tag_name"] = tag
                source = self.source(
                    {"tag-pattern": pattern, "source-archive": "tarball"}
                )
                await self.checker.check(source)
                self.assertEqual(source.new_version.version, version)

    async def test_pattern_mismatch_and_version_template(self):
        source = self.source({"source-archive": "tarball", "tag-pattern": r"v(2.*)"})
        with self.assertRaisesRegex(CheckerQueryError, "does not match"):
            await self.checker.check(source)
        source = self.source(
            {
                "asset-name": "app-v${major}.${minor}.${patch}-linux.tar.xz",
                "tag-pattern": r"v(.+)",
            }
        )
        await self.checker.check(source)
        self.assertEqual(source.new_version.version, "1.2.3")

    async def test_unchanged_and_untrusted_release_link(self):
        source = self.source(
            {"source-archive": "tarball"}, url=self.release["tarball_url"]
        )
        await self.checker.check(source)
        self.assertIsNone(source.new_version)
        self.download.assert_not_awaited()
        for link in (
            None,
            "javascript:bad",
            "https://other.org/example/app/releases/tag/v1.2.3",
        ):
            with self.subTest(link=link):
                self.fetch.return_value = copy.deepcopy(self.release) | {
                    "html_url": link,
                    "published_at": None,
                }
                source = self.source({"source-archive": "zipball"})
                await self.checker.check(source)
                self.assertIsNone(source.new_release_url)
                self.assertIsNone(source.new_version.timestamp)
