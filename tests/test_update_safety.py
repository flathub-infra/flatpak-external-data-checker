import datetime
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from lxml import etree

from src.checkers.debianrepochecker import DebianRepoChecker
from src.checkers.gitchecker import GitChecker
from src.checkers.htmlchecker import HTMLChecker
from src.checkers.jsonchecker import JSONChecker
from src.checkers.urlchecker import URLChecker
from src.lib.checksums import MultiDigest
from src.lib.errors import SourceLoadError
from src.lib.externaldata import ExternalBase, ExternalFile
from src.lib.utils import compare_versions, version_from_template
from src.lib.version import compare_appstream_versions
from src.manifest import CheckerOptions, ManifestChecker


class TestUpdateSafety(unittest.IsolatedAsyncioTestCase):
    async def run_update(
        self,
        current,
        candidate,
        *,
        allow=False,
        history=None,
        pattern=True,
        same_url=False,
        url_suffix="",
        with_metainfo=True,
        require_important=False,
        important_property="is-main-source",
    ):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "org.test.App.json"
            metainfo = Path(directory) / "org.test.App.metainfo.xml"
            metadata = {
                "type": "rotating-url",
                "url": "https://example.org/latest",
                "pattern": r"https://example.org/app-([\w.+-]+)\.tar\.gz",
                important_property: True,
                "allow-downgrade": allow,
            }
            if not pattern:
                metadata.pop("pattern")
            source = {
                "type": "extra-data",
                "url": f"https://example.org/app-{current}.tar.gz",
                "sha256": "old",
                "size": 1,
                "x-checker-data": metadata,
            }
            path.write_text(
                json.dumps(
                    {
                        "id": "org.test.App",
                        "modules": [{"name": "app", "sources": [source]}],
                    }
                )
            )
            before = (
                "<component><id>org.test.App</id><releases>"
                + (history or '<release version="1.1.0" date="2020-01-01"/>')
                + "</releases></component>"
            )
            if with_metainfo:
                metainfo.write_text(before)
            checker = ManifestChecker(
                str(path), CheckerOptions(require_important_update=require_important)
            )
            data = checker.get_external_data()[0]
            remote = data.current_version._replace(
                url=(
                    data.current_version.url
                    if same_url
                    else f"https://example.org/app-{candidate}.tar.gz{url_suffix}"
                ),
                version=candidate,
                timestamp=datetime.datetime(2020, 2, 2, tzinfo=datetime.timezone.utc),
                checksum=MultiDigest(sha256="new"),
            )
            with patch(
                "src.lib.utils.get_extra_data_info_from_url",
                new=AsyncMock(return_value=remote),
            ):
                await URLChecker(None).check(data)
            changes = checker.update_manifests()
            saved = json.loads(path.read_text())["modules"][0]["sources"][0]
            self.assertEqual(saved, data.source)
            self.assertNotIn("current-version", saved["x-checker-data"])
            return (
                data,
                changes,
                metainfo.read_text() if with_metainfo else None,
                before,
            )

    async def test_downgrade_does_not_update_manifest_or_appstream(self):
        data, changes, after, before = await self.run_update("1.1.0", "1.0.1")
        self.assertIsNone(data.new_version)
        self.assertNotIn(data.State.OUTDATED, data.state)
        self.assertEqual(changes, [])
        self.assertEqual(after, before)

    async def test_intentional_rollback_preserves_appstream(self):
        data, changes, after, before = await self.run_update(
            "1.1.0",
            "1.0.1",
            allow=True,
            history='<release version="1.1.0" date="2020-01-01"/>'
            '<release version="1.0.1" date="2019-01-01"/>',
        )
        self.assertTrue(changes)
        self.assertIn("1.0.1", data.source["url"])
        self.assertEqual(after, before)

    async def test_opaque_source_keeps_opt_in_for_post_rollback_updates(self):
        for current, candidate in (("1.1.0", "1.0.1"), ("1.0.1", "1.0.2")):
            with self.subTest(current=current, candidate=candidate):
                data, changes, after, before = await self.run_update(
                    current,
                    candidate,
                    allow=True,
                    pattern=False,
                )
                self.assertTrue(changes)
                self.assertIn(candidate, data.source["url"])
                self.assertEqual(after, before)

    async def test_same_version_checksum_repair_preserves_appstream(self):
        data, changes, after, before = await self.run_update("1.1.0", "1.1.0")
        self.assertTrue(changes)
        self.assertEqual(data.source["sha256"], "new")
        self.assertEqual(after, before)

    async def test_important_checksum_repairs_do_not_require_a_new_release(self):
        for marker in ("is-main-source", "is-important"):
            for suffix in ("", "?download=1"):
                with self.subTest(marker=marker, suffix=suffix):
                    data, changes, after, before = await self.run_update(
                        "1.1.0",
                        "1.1.0",
                        url_suffix=suffix,
                        require_important=True,
                        important_property=marker,
                    )
                    self.assertTrue(changes)
                    self.assertEqual(data.source["sha256"], "new")
                    self.assertFalse(data.has_version_changed)
                    self.assertEqual(after, before)

    async def test_same_version_changed_url_repairs_without_new_release(self):
        data, changes, after, before = await self.run_update(
            "1.1.0", "1.1.0", url_suffix="?download=1"
        )
        self.assertTrue(changes)
        self.assertEqual(data.source["sha256"], "new")
        self.assertTrue(data.source["url"].endswith("?download=1"))
        self.assertFalse(data.has_version_changed)
        self.assertEqual(after, before)

    async def test_equivalent_version_spelling_does_not_add_release(self):
        data, changes, after, before = await self.run_update("1.1", "1.1.0")
        self.assertTrue(changes)
        self.assertEqual(data.source["sha256"], "new")
        self.assertFalse(data.has_version_changed)
        self.assertEqual(after, before)

    async def test_newer_source_older_than_appstream_preserves_history(self):
        _, changes, after, before = await self.run_update("1.0.0", "1.0.1")
        self.assertTrue(changes)
        self.assertEqual(after, before)

    async def test_appstream_ordering_is_checked_independently(self):
        _, changes, after, before = await self.run_update(
            "0.9", "1.0", history='<release version="1.0rc1" date="2020-01-01"/>'
        )
        self.assertTrue(changes)
        self.assertEqual(after, before)

    async def test_appstream_timestamp_does_not_allow_older_release_date(self):
        _, changes, after, before = await self.run_update(
            "1.1.0",
            "1.2.0",
            history='<release version="1.1.0" timestamp="1609459200"/>',
        )
        self.assertTrue(changes)
        self.assertEqual(after, before)

    async def test_upgrade_adds_release_without_new_metadata(self):
        _, changes, after, _ = await self.run_update("1.1.0", "1.2.0")
        self.assertTrue(changes)
        self.assertEqual(
            [
                r.get("version")
                for r in etree.fromstring(after.encode()).findall("releases/release")
            ],
            ["1.2.0", "1.1.0"],
        )

    async def test_unknown_current_keeps_compatibility(self):
        data, changes, _, _ = await self.run_update(
            "1.1.0", "1.0.1", pattern=False, with_metainfo=False
        )
        self.assertTrue(changes)
        self.assertIsNone(data.current_version.version)

    async def test_metainfo_blocks_downgrade_without_url_pattern(self):
        data, changes, after, before = await self.run_update(
            "1.1.0", "1.0.1", pattern=False
        )
        self.assertEqual(data.current_version.version, "1.1.0")
        self.assertIsNone(data.new_version)
        self.assertEqual(changes, [])
        self.assertEqual(after, before)

    async def test_unknown_candidate_does_not_replace_known_version(self):
        data, changes, after, before = await self.run_update(
            "1.1.0", None, pattern=False
        )
        self.assertIsNone(data.new_version)
        self.assertEqual(changes, [])
        self.assertEqual(after, before)

    async def test_comparison_unavailable_does_not_update(self):
        with patch("src.lib.utils.compare_versions", return_value=None):
            data, changes, after, before = await self.run_update("1.1.0", "1.2.0")
        self.assertIsNone(data.new_version)
        self.assertEqual(changes, [])
        self.assertEqual(after, before)

    async def test_non_semver_downgrade(self):
        data, changes, _, _ = await self.run_update("release-10", "release-9")
        self.assertIsNone(data.new_version)
        self.assertEqual(changes, [])

    async def test_stable_url_new_version_is_not_checksum_only(self):
        data, changes, after, _ = await self.run_update(
            "1.1.0", "1.2.0", pattern=False, same_url=True
        )
        self.assertTrue(changes)
        self.assertIn(data.State.OUTDATED, data.state)
        self.assertTrue(data.has_version_changed)
        self.assertIn('version="1.2.0"', after)

    async def test_git_constraints_do_not_authorize_downgrades(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "org.test.App.json"
            path.write_text(
                json.dumps(
                    {
                        "id": "org.test.App",
                        "modules": [
                            {
                                "name": "app",
                                "sources": [
                                    {
                                        "type": "git",
                                        "url": "https://example.org/app.git",
                                        "tag": "v1.1.0",
                                        "commit": "new",
                                        "x-checker-data": {
                                            "type": "git",
                                            "versions": {"<": "1.1.0"},
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                )
            )
            checker = ManifestChecker(str(path))
            data = checker.get_external_data()[0]
            with patch(
                "src.checkers.gitchecker.git_ls_remote",
                new=AsyncMock(
                    return_value={
                        "refs/tags/v1.0.1": "old",
                        "refs/tags/v1.1.0": "new",
                    }
                ),
            ):
                await GitChecker(None).check(data)
            self.assertIsNone(data.new_version)
            self.assertEqual(checker.update_manifests(), [])
            self.assertEqual(data.source["tag"], "v1.1.0")


class TestVersionSafety(unittest.TestCase):
    def test_discovery_leaves_invalid_checker_metadata_for_validation(self):
        for metadata in (
            {"type": "git", "tag-pattern": 1},
            {"type": "anitya", "tag-template": ["$version"]},
        ):
            with self.subTest(metadata=metadata):
                data = ExternalBase.from_source(
                    "manifest.json",
                    {
                        "type": "git",
                        "url": "https://example.org/app.git",
                        "tag": "v1.1.0",
                        "x-checker-data": metadata,
                    },
                )
                data.discover_current_version()
                self.assertIsNone(data.current_version.version)

    def test_appstream_ordering(self):
        for candidate, current, expected in (
            ("1.0", "1.0rc1", -1),
            ("1.0", "1.0~rc1", 1),
            ("1.0rc0a", "1.0rc", 0),
            ("1.0a0b-0", "1.0a-2", -1),
            ("1.0-1", "1.0", 1),
            ("2:1.0", "1:9.0", 1),
            ("1.0.0001", "1.0.1", 0),
            ("release-10", "release-9", 1),
            ("1.0", "1.0.0", -1),
            ("1.0~rc2", "1.0~rc10", -1),
            ("1.0+a", "1.0a", 1),
            ("1.0", None, None),
            ("", "1.0", None),
            ("invalid:1.0", "1.0", None),
            ("1:2:3", "1.0", None),
            ("版本1", "1.0", None),
        ):
            with self.subTest(candidate=candidate, current=current):
                self.assertEqual(
                    compare_appstream_versions(candidate, current), expected
                )
                if expected is not None:
                    self.assertEqual(
                        compare_appstream_versions(current, candidate), -expected
                    )

    def test_versions_from_existing_sources(self):
        for source, metadata, expected in (
            (
                {"type": "git", "url": "https://example.org/app.git", "tag": "v1.1.0"},
                {"type": "git"},
                "1.1.0",
            ),
            (
                {"type": "git", "url": "https://example.org/app.git", "tag": "v1.1.0"},
                {"type": "git", "tag-pattern": r"v[\d.]+"},
                "v1.1.0",
            ),
            (
                {
                    "type": "git",
                    "url": "https://example.org/app.git",
                    "tag": "release-10",
                },
                {"type": "github", "tag-pattern": r"release-(\d+)"},
                "10",
            ),
            (
                {
                    "type": "git",
                    "url": "https://example.org/app.git",
                    "tag": "v1.1-junk",
                },
                {"type": "forgejo", "tag-pattern": r"v([\d.]+)"},
                None,
            ),
            (
                {
                    "type": "git",
                    "url": "https://example.org/app.git",
                    "tag": "release-10",
                },
                {"type": "anitya", "tag-template": "release-$version"},
                "10",
            ),
            (
                {
                    "type": "archive",
                    "url": "https://example.org/app-1.1.0.tar.gz",
                    "sha256": "old",
                },
                {
                    "type": "anitya",
                    "url-template": "https://example.org/app-$version.tar.gz",
                },
                "1.1.0",
            ),
        ):
            with self.subTest(source=source, metadata=metadata):
                data = ExternalBase.from_source(
                    "manifest.json",
                    {
                        **source,
                        "x-checker-data": metadata,
                    },
                )
                data.discover_current_version()
                self.assertEqual(data.current_version.version, expected)

    def test_template_inference_does_not_guess(self):
        for template, value, expected in (
            ("app-$version.tar.gz", "app-1.1.0.tar.gz", "1.1.0"),
            ("${version}/app-$version", "1.1/app-1.1", "1.1"),
            ("${version}/app-$version", "1.1/app-1.0", None),
            ("$major/app-$version", "1/app-1.1", None),
            ("app-$version", "other-1.1", None),
            ("app-$version", "app-", None),
            ("app-$$-$version", "app-$-1.1", "1.1"),
            ("app-$version$", "app-1.1$", None),
            (None, "app-1.1", None),
        ):
            with self.subTest(template=template, value=value):
                self.assertEqual(version_from_template(template, value), expected)

    def test_comparison_uses_existing_schemes(self):
        for candidate, current, scheme, expected in (
            ("1.0.1", "1.1.0", "loose", -1),
            ("release-10", "release-9", "loose", 1),
            ("1.0", "1.0.0", "loose", 0),
            ("1.0.0+build.2", "1.0.0+build.1", "semantic", 0),
            ("1.0.0-rc.1", "1.0.0", "semantic", -1),
            ("not-semver", "1.0.0", "semantic", None),
            ("1.0~rc1", "1.0", "debian", -1),
            ("1.0", "1.0~rc1", "debian", 1),
            (None, "1.0", "loose", None),
            ("1.0", None, "loose", None),
        ):
            with self.subTest(candidate=candidate, current=current, scheme=scheme):
                self.assertEqual(compare_versions(candidate, current, scheme), expected)

    def test_invalid_safety_metadata_is_rejected(self):
        for metadata in ({"allow-downgrade": "true"},):
            with self.subTest(metadata=metadata), self.assertRaises(SourceLoadError):
                ExternalBase.from_source(
                    "manifest.json",
                    {
                        "type": "archive",
                        "url": "https://example.org/app.tar.gz",
                        "sha256": "old",
                        "x-checker-data": {"type": "json", **metadata},
                    },
                )


class TestMetainfoDiscovery(unittest.IsolatedAsyncioTestCase):
    def make_checker(
        self,
        directory,
        history,
        *,
        main=True,
        dependency=False,
        template=False,
        checker_metadata=None,
        source_url=None,
    ):
        path = Path(directory) / "org.test.App.json"
        sources = []
        if dependency:
            sources.append(
                {
                    "type": "extra-data",
                    "url": "https://example.org/dependency",
                    "sha256": "old",
                    "size": 1,
                    "x-checker-data": {
                        "type": "json",
                        "url": "https://example.org/dependency.json",
                        "version-query": ".version",
                        "url-query": ".url",
                    },
                }
            )
        metadata = {
            "type": "json",
            "url": "https://example.org/app.json",
            "version-query": ".version",
            "url-query": ".url",
        }
        if checker_metadata:
            metadata.update(checker_metadata)
        if main:
            metadata["is-main-source"] = True
        if template:
            metadata["type"] = "anitya"
            metadata["project-id"] = 1
            metadata["url-template"] = "https://example.org/app-$version.tar.gz"
        sources.append(
            {
                "type": "extra-data",
                "url": source_url
                or (
                    "https://example.org/app-1.0.0.tar.gz"
                    if template
                    else "https://example.org/download"
                ),
                "sha256": "old",
                "size": 1,
                "x-checker-data": metadata,
            }
        )
        path.write_text(
            json.dumps(
                {"id": "org.test.App", "modules": [{"name": "app", "sources": sources}]}
            )
        )
        if history is not None:
            (Path(directory) / "org.test.App.metainfo.xml").write_text(history)
        return ManifestChecker(str(path))

    async def test_debian_ordering_through_update_path(self):
        for current, candidate, allow, expected_update in (
            ("1.0", "1.0~rc1", False, False),
            ("1.0~rc1", "1.0", False, True),
            ("1.0", "1.0~rc1", True, True),
            ("1.0", "1.0", False, True),
        ):
            with (
                self.subTest(current=current, candidate=candidate, allow=allow),
                tempfile.TemporaryDirectory() as directory,
            ):
                checker = self.make_checker(
                    directory,
                    f'<component><releases><release version="{current}" '
                    'date="2020-01-01"/></releases></component>',
                    checker_metadata={
                        "type": "debian-repo",
                        "package-name": "app",
                        "root": "https://example.org/repo",
                        "dist": "stable",
                        "component": "main",
                        "allow-downgrade": allow,
                    },
                )
                checker._checkers = [DebianRepoChecker]
                data = checker.get_external_data()[0]
                cache = MagicMock()
                cache.__enter__.return_value = {
                    "app": SimpleNamespace(
                        candidate=SimpleNamespace(
                            uri=f"https://example.org/app-{candidate}.deb",
                            version=candidate,
                            size=1,
                            record={"SHA256": "new"},
                        )
                    )
                }
                with (
                    patch.object(DebianRepoChecker, "_load_repo", return_value=cache),
                    patch.object(
                        DebianRepoChecker,
                        "_get_timestamp_for_candidate",
                        new=AsyncMock(
                            return_value=datetime.datetime(
                                2020, 2, 2, tzinfo=datetime.timezone.utc
                            )
                        ),
                    ),
                ):
                    await checker.check()
                changes = checker.update_manifests()
                self.assertEqual(bool(changes), expected_update)
                self.assertEqual(
                    data.source["sha256"], "new" if expected_update else "old"
                )
                if expected_update:
                    self.assertEqual(data.has_version_changed, candidate != current)

    async def test_relative_html_templates_prevent_downgrade_with_stale_metainfo(self):
        for template in (
            "app-$version.tar.gz",
            "/downloads/app-$version.tar.gz",
            "//example.org/downloads/app-$version.tar.gz",
            "https://example.org/downloads/app-$version.tar.gz",
        ):
            with (
                self.subTest(template=template),
                tempfile.TemporaryDirectory() as directory,
            ):
                checker = self.make_checker(
                    directory,
                    '<component><releases><release version="1.0.0" '
                    'date="2020-01-01"/></releases></component>',
                    checker_metadata={
                        "type": "html",
                        "url": "https://example.org/downloads/",
                        "version-pattern": r"version: ([\d.]+)",
                        "url-template": template,
                    },
                    source_url="https://example.org/downloads/app-1.1.0.tar.gz",
                )
                checker._checkers = [HTMLChecker]
                data = checker.get_external_data()[0]
                remote = ExternalFile(
                    url="https://example.org/downloads/app-1.0.1.tar.gz",
                    version=None,
                    timestamp=datetime.datetime(
                        2020, 2, 2, tzinfo=datetime.timezone.utc
                    ),
                    checksum=MultiDigest(sha256="new"),
                    size=1,
                )
                with (
                    patch.object(
                        HTMLChecker,
                        "_get_text",
                        new=AsyncMock(return_value="version: 1.0.1"),
                    ),
                    patch(
                        "src.lib.utils.get_extra_data_info_from_url",
                        new=AsyncMock(return_value=remote),
                    ),
                ):
                    await checker.check()
                self.assertEqual(checker.update_manifests(), [])
                self.assertEqual(data.current_version.version, "1.1.0")
                self.assertEqual(data.source["sha256"], "old")

    async def test_real_json_update_path_uses_metainfo_only_for_main_source(self):
        with tempfile.TemporaryDirectory() as directory:
            checker = self.make_checker(
                directory,
                "<component><id>org.test.App</id><releases>"
                '<release version="1.1.0" date="2020-01-01"/></releases></component>',
                dependency=True,
            )
            dep, app = checker.get_external_data()
            self.assertIsNone(dep.current_version.version)
            self.assertEqual(app.current_version.version, "1.1.0")
            checker._checkers = [JSONChecker]

            async def response(url):
                name = "dependency" if "dependency" in url else "app"
                return {
                    "version": "1.0.1",
                    "url": f"https://example.org/{name}-1.0.1.tar.gz",
                }

            async def download(*, url, **kwargs):
                return ExternalFile(
                    url=url,
                    version=None,
                    timestamp=None,
                    checksum=MultiDigest(sha256="new"),
                    size=1,
                )

            with (
                patch.object(
                    JSONChecker, "_get_json", new=AsyncMock(side_effect=response)
                ),
                patch(
                    "src.lib.utils.get_extra_data_info_from_url",
                    new=AsyncMock(side_effect=download),
                ),
            ):
                await checker.check()
            self.assertIsNone(app.new_version)
            self.assertIsNotNone(dep.new_version)
            self.assertEqual(len(checker.update_manifests()), 1)
            self.assertEqual(dep.source["sha256"], "new")
            self.assertEqual(app.source["sha256"], "old")

    async def test_source_version_takes_precedence_over_metainfo(self):
        with tempfile.TemporaryDirectory() as directory:
            checker = self.make_checker(
                directory,
                '<component><releases><release version="1.1.0"/>'
                "</releases></component>",
                template=True,
            )
            self.assertEqual(
                checker.get_external_data()[0].current_version.version, "1.0.0"
            )

    async def test_default_main_source_selection_uses_metainfo(self):
        with tempfile.TemporaryDirectory() as directory:
            checker = self.make_checker(
                directory,
                '<component><releases><release version="1.1.0"/>'
                "</releases></component>",
                main=False,
            )
            self.assertEqual(
                checker.get_external_data()[0].current_version.version, "1.1.0"
            )

    async def test_development_release_is_not_treated_as_an_older_stable_version(self):
        with tempfile.TemporaryDirectory() as directory:
            checker = self.make_checker(
                directory,
                '<component><releases><release type="development" version="2.0"/>'
                '<release version="1.1.0"/></releases></component>',
            )
            self.assertEqual(
                checker.get_external_data()[0].current_version.version, "2.0"
            )

    async def test_unusable_metainfo_leaves_version_unknown(self):
        for history in (
            None,
            "<component>",
            "<component/>",
            '<component><releases type="external"/></component>',
            '<component><id>org.other.App</id><releases><release version="2.0"/>'
            "</releases></component>",
        ):
            with (
                self.subTest(history=history),
                tempfile.TemporaryDirectory() as directory,
            ):
                checker = self.make_checker(directory, history)
                self.assertIsNone(
                    checker.get_external_data()[0].current_version.version
                )
