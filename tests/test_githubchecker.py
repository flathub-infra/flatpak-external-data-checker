import copy
import datetime
import unittest
from unittest import mock

from src.checkers.githubchecker import GitHubChecker
from src.lib.checksums import MultiDigest
from src.lib.errors import CheckerMetadataError, CheckerQueryError
from src.lib.externaldata import ExternalBase, ExternalFile, ExternalGitRepo
from src.lib.sourceupdates import SourceUpdate
from src.manifest import ManifestChecker
from tests.support import TAG_PATTERN_CASES

REPO = "pulsar-edit/pulsar"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
TAG = "v1.02.3"
PAGE = f"https://github.com/{REPO}/releases/tag/{TAG}"
DATE = "2026-11-04T12:00:00Z"


def asset(name):
    return {
        "name": name,
        "state": "uploaded",
        "browser_download_url": f"https://github.com/{REPO}/releases/download/{TAG}/{name}",
    }


RELEASE = {
    "tag_name": TAG,
    "draft": False,
    "prerelease": False,
    "published_at": DATE,
    "html_url": PAGE,
    "tarball_url": f"https://api.github.com/repos/{REPO}/tarball/{TAG}",
    "zipball_url": f"https://api.github.com/repos/{REPO}/zipball/{TAG}",
    "assets": [
        asset("Linux.pulsar_1.02.3_amd64.deb"),
        asset("ARM.Linux.pulsar_1.02.3_arm64.deb"),
        asset("Linux.pulsar_1.02.3_amd64.deb.sha256"),
    ],
}


class TestGitHubChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.checker = GitHubChecker(mock.MagicMock())
        self.release = copy.deepcopy(RELEASE)
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
            size=1234,
        )

    def source(self, metadata=None, source_type="file", **extra):
        source = {
            "type": source_type,
            "url": f"https://github.com/{REPO}/releases/download/v1/old.deb",
            "sha256": "a" * 64,
            "x-checker-data": {"type": "github", "repo": REPO, **(metadata or {})},
            **extra,
        }
        if source_type == "git":
            source["url"] = extra.get("url", f"https://github.com/{REPO}.git")
            source["tag"] = "v1"
            source["commit"] = "a" * 40
        return ExternalBase.from_source("test.json", source)

    async def test_pulsar_architectures_and_release_metadata(self):
        for name, expected in (
            ("Linux.pulsar_${version}_amd64.deb", RELEASE["assets"][0]),
            (
                "ARM.Linux.pulsar_${major}.${minor}.${patch}_arm64.deb",
                RELEASE["assets"][1],
            ),
        ):
            with self.subTest(name=name):
                source = self.source(
                    {"tag-pattern": r"^v([\d.]+)$", "asset-name": name}
                )
                await self.checker.validate_checker_data(source)
                await self.checker.check(source)
                self.assertEqual(
                    source.new_version.url, expected["browser_download_url"]
                )
                self.assertEqual(source.new_version.version, "1.02.3")
                self.assertEqual(source.new_version.checksum.sha256, "b" * 64)
                self.assertEqual(source.new_version.size, 1234)
                self.assertEqual(
                    source.new_version.timestamp,
                    datetime.datetime(2026, 11, 4, 12, tzinfo=datetime.timezone.utc),
                )
                self.assertEqual(SourceUpdate.from_source(source).release_url, PAGE)
                source.checker_data["release-url-template"] = (
                    "https://example.org/$version"
                )
                self.assertEqual(
                    SourceUpdate.from_source(source).release_url,
                    "https://example.org/1.02.3",
                )
        self.assertEqual(self.fetch.await_args_list, [mock.call(API), mock.call(API)])

    async def test_raw_tag_and_tag_template(self):
        self.release["assets"] = [asset(f"source-{TAG}.tar.gz")]
        source = self.source({"asset-name": "source-${tag}.tar.gz"})
        await self.checker.check(source)
        self.assertEqual(source.new_version.version, TAG)
        self.assertTrue(source.new_version.url.endswith(f"source-{TAG}.tar.gz"))

    async def test_tag_pattern_extraction(self):
        for pattern, tag, version in TAG_PATTERN_CASES:
            with self.subTest(pattern=pattern, tag=tag):
                self.release["tag_name"] = tag
                self.release["assets"] = [asset(f"app-{version}.tar.gz")]
                source = self.source(
                    {"tag-pattern": pattern, "asset-name": "app-${version}.tar.gz"}
                )
                await self.checker.check(source)
                self.assertEqual(source.new_version.version, version)
                self.assertTrue(
                    source.new_version.url.endswith(f"app-{version}.tar.gz")
                )

    async def test_release_link_reaches_metainfo(self):
        source = self.source({"source-archive": "tarball"})
        await self.checker.check(source)
        manifest = mock.Mock()
        manifest.app_id = "org.example.App"
        manifest._root_manifest_path = "test.json"
        manifest._blocked_update_sources = set()
        manifest.get_external_data.return_value = [source]
        manifest._main_sources.side_effect = lambda: ManifestChecker._main_sources(
            manifest
        )
        with (
            mock.patch("src.manifest.find_appdata_file", return_value="metainfo.xml"),
            mock.patch("src.manifest.add_release_to_file") as add_release,
        ):
            ManifestChecker._update_appdata(manifest)
        add_release.assert_called_once_with(
            "metainfo.xml", TAG, "2026-11-04", None, release_url=PAGE
        )

    async def test_source_archives(self):
        for kind in ("tarball", "zipball"):
            with self.subTest(kind=kind):
                source = self.source({"source-archive": kind}, "archive")
                await self.checker.validate_checker_data(source)
                await self.checker.check(source)
                self.assertEqual(source.new_version.url, RELEASE[f"{kind}_url"])
                self.assertEqual(source.new_release_url, PAGE)

    async def test_git_resolves_release_tag_and_keeps_publication_date(self):
        source = self.source({"tag-pattern": r"v(.+)"}, "git")
        with mock.patch(
            "src.lib.utils.git_ls_remote",
            return_value={
                f"refs/tags/{TAG}": "tag-object",
                f"refs/tags/{TAG}^{{}}": "b" * 40,
            },
        ) as refs:
            await self.checker.validate_checker_data(source)
            await self.checker.check(source)
        refs.assert_awaited_once_with(f"https://github.com/{REPO}.git")
        self.assertIsInstance(source, ExternalGitRepo)
        self.assertEqual(source.new_version.tag, TAG)
        self.assertEqual(source.new_version.commit, "b" * 40)
        self.assertEqual(source.new_version.version, "1.02.3")
        self.assertEqual(
            source.new_version.timestamp.isoformat(), "2026-11-04T12:00:00+00:00"
        )
        self.assertEqual(source.new_release_url, PAGE)
        self.download.assert_not_awaited()

    async def test_git_repository_mismatch(self):
        source = self.source({}, "git", url="https://github.com/other/repo.git")
        with self.assertRaisesRegex(CheckerMetadataError, "Git source must"):
            await self.checker.check(source)
        self.fetch.assert_not_awaited()

    async def test_missing_ambiguous_and_unfinished_assets(self):
        name = RELEASE["assets"][0]["name"]
        for assets, count in (
            (RELEASE["assets"][1:], 0),
            ([asset(name), asset(name)], 2),
            ([dict(asset(name), state="open")], 0),
        ):
            with self.subTest(assets=assets):
                self.release["assets"] = assets
                source = self.source({"asset-name": name})
                with self.assertRaisesRegex(CheckerQueryError, f"found {count}") as err:
                    await self.checker.check(source)
                self.assertIn(REPO, str(err.exception))
                self.assertIn(TAG, str(err.exception))
                self.assertIn(name, str(err.exception))
                self.assertIsNone(source.new_version)
        self.download.assert_not_awaited()

    async def test_invalid_release_data_does_not_update(self):
        for change in (
            {"draft": True},
            {"prerelease": True},
            {"tag_name": None},
            {"tag_name": ""},
            {"assets": None},
            {"published_at": 123},
            {"published_at": "not-a-date"},
            {
                "assets": [
                    dict(RELEASE["assets"][0], browser_download_url="file:///tmp/a")
                ]
            },
        ):
            with self.subTest(change=change):
                self.fetch.return_value = RELEASE | change
                source = self.source({"asset-name": RELEASE["assets"][0]["name"]})
                with self.assertRaises(CheckerQueryError):
                    await self.checker.check(source)
                self.assertIsNone(source.new_version)
        self.download.assert_not_awaited()

    async def test_tag_pattern_must_match_latest_release(self):
        for pattern in (r"v(2\..+)", r"v(1)", r"v(.*)?1.02.3", r"v2.*", r"v1"):
            with self.subTest(pattern=pattern):
                source = self.source(
                    {"tag-pattern": pattern, "source-archive": "tarball"}
                )
                with self.assertRaisesRegex(CheckerQueryError, "does not match"):
                    await self.checker.check(source)
        self.download.assert_not_awaited()

    async def test_unchanged_asset_does_not_download(self):
        source = self.source(
            {"asset-name": RELEASE["assets"][0]["name"]},
            url=RELEASE["assets"][0]["browser_download_url"],
        )
        await self.checker.check(source)
        self.download.assert_not_awaited()
        self.assertIsNone(source.new_version)
        self.assertTrue(source.state & source.State.LATEST)
        self.assertIsNone(source.new_release_url)

    async def test_optional_release_link_and_date(self):
        for link in (
            None,
            "javascript:bad",
            "https://github.com/other/repo/releases/tag/v1",
        ):
            with self.subTest(link=link):
                self.release.update(html_url=link, published_at=None)
                source = self.source({"source-archive": "tarball"})
                await self.checker.check(source)
                self.assertIsNotNone(source.new_version)
                self.assertIsNone(source.new_release_url)
                self.assertIsNone(source.new_version.timestamp)
