import copy
import os
import unittest
from unittest import mock

import aiohttp

from src.checkers.gitchecker import GitChecker, TagWithSemver, TagWithVersion
from src.lib.errors import CheckerFetchError, CheckerQueryError
from src.lib.externaldata import ExternalGitRef, ExternalGitRepo
from src.lib.sourceupdates import SourceUpdate
from src.lib.utils import init_logging
from src.manifest import ManifestChecker
from tests.support import TAG_PATTERN_CASES, upstream_test

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.virustotal.Uploader.yml")


class TestGitChecker(unittest.IsolatedAsyncioTestCase):
    async def test_tag_pattern_extraction(self):
        for pattern, tag, version in (
            *TAG_PATTERN_CASES,
            (r"^[0-9.]+", "1.2.3-suffix", "1.2.3"),
        ):
            with self.subTest(pattern=pattern, tag=tag):
                data = ExternalGitRepo.from_source_impl(
                    source_path="test.yml",
                    source={
                        "type": "git",
                        "url": "https://example.org/app.git",
                        "tag": tag.replace("1.2.3", "0.1.0"),
                        "x-checker-data": {"type": "git", "tag-pattern": pattern},
                    },
                )
                with mock.patch(
                    "src.checkers.gitchecker.git_ls_remote",
                    return_value={f"refs/tags/{tag}": "new"},
                ):
                    await GitChecker._check_has_new(data)
                self.assertEqual(data.new_version.version, version)
                self.assertEqual(data.new_version.tag, tag)

    async def test_sorted_tag_updates_require_newer_version(self):
        cases = (
            ("v2.1.0-pre1", "v2.0.0", {}, False),
            ("v2.1.0-pre1", "v2.1.0", {}, True),
            ("v1.0.0", "v2.0.0", {}, True),
            ("unknown", "v2.0.0", {}, False),
            ("v2.0.0", "v1.0.0", {"versions": {"<": "2.0.0"}}, False),
            ("v1.0.0", "v2.0.0", {"versions": {"<": "2.0.0"}}, False),
            ("v2.0.0", "v1.0.0", {"sort-tags": False}, True),
            ("unknown", "v2.0.0", {"sort-tags": False}, True),
            ("v2.0.0", "v2.0.0", {}, True),
            (None, "v2.0.0", {}, True),
        )
        for scheme in ("loose", "semantic"):
            for current, candidate, options, update in cases:
                for current_present in (True, False):
                    with self.subTest(
                        scheme=scheme,
                        current=current,
                        candidate=candidate,
                        options=options,
                        current_present=current_present,
                    ):
                        data = ExternalGitRepo.from_source_impl(
                            source_path="test.git",
                            source={
                                "type": "git",
                                "url": "https://example.org/test.git",
                                "tag": current,
                                "commit": "old",
                                "x-checker-data": {
                                    "type": "git",
                                    "tag-pattern": r"^v([\d.]+)$",
                                    "version-scheme": scheme,
                                    **options,
                                },
                            },
                        )
                        refs = {}
                        if current_present and current:
                            refs[f"refs/tags/{current}"] = "old"
                        refs[f"refs/tags/{candidate}"] = "new"
                        # The upper-bound case needs an eligible unchanged tag.
                        if options.get("versions") == {"<": "2.0.0"}:
                            refs.setdefault("refs/tags/v1.0.0", "old")
                        with mock.patch(
                            "src.checkers.gitchecker.git_ls_remote", return_value=refs
                        ):
                            await GitChecker._check_has_new(data)
                        if update:
                            self.assertIsNotNone(data.new_version)
                            self.assertEqual(data.new_version.tag, candidate)
                            self.assertEqual(data.new_version.commit, "new")
                        else:
                            self.assertIsNone(data.new_version)
                            self.assertNotIn(data.State.OUTDATED, data.state)

    async def test_tag_comparison_uses_pattern_and_scheme(self):
        for current, candidate, pattern, scheme, update in (
            ("2.0.0", "1.0.0", r"^[\d.]+$", "loose", False),
            ("1.0.0", "2.0.0", r"^[\d.]+$", "loose", True),
            ("2.0.0", "1.0.0", r"^[\d.]+$", "semantic", False),
            ("1.0.0", "2.0.0", r"^[\d.]+$", "semantic", True),
            ("release-1.0.0", "release-2.0.0", r"^release-([\d.]+)$", "loose", True),
            ("release-next", "release-2.0.0", r"^release-([\d.]+)$", "loose", False),
            ("v2.1.0-pre1", "release-2.0.0", r"^release-([\d.]+)$", "loose", False),
            ("v2.1.0-pre1", "v2.1.0", r"^v(.+)$", "semantic", True),
            ("v2.1.0-pre1", "v2.0.0", r"^v(.+)$", "semantic", False),
            ("v2.0", "v3.0.0", r"^v(.+)$", "semantic", False),
            ("v1.a", "v2.0", r"^v(.+)$", "loose", True),
            ("v1.foo", "v1.2", r"^v(.+)$", "loose", False),
            ("v2.0.0", "V2.0.0", r"^[vV](.+)$", "loose", False),
            ("v2.0.0+one", "v2.0.0+two", r"^v(.+)$", "semantic", False),
        ):
            with self.subTest(current=current, candidate=candidate, scheme=scheme):
                data = ExternalGitRepo.from_source_impl(
                    source_path="test.git",
                    source={
                        "type": "git",
                        "url": "https://example.org/test.git",
                        "tag": current,
                        "commit": "old",
                        "x-checker-data": {
                            "type": "git",
                            "tag-pattern": pattern,
                            "version-scheme": scheme,
                        },
                    },
                )
                with mock.patch(
                    "src.checkers.gitchecker.git_ls_remote",
                    return_value={f"refs/tags/{candidate}": "new"},
                ):
                    await GitChecker._check_has_new(data)
                self.assertEqual(data.new_version is not None, update)

    async def test_same_tag_peeled_commit_change(self):
        data = ExternalGitRepo.from_source_impl(
            source_path="test.git",
            source={
                "type": "git",
                "url": "https://example.org/test.git",
                "tag": "v2.0.0",
                "commit": "old",
                "x-checker-data": {"type": "git"},
            },
        )
        with mock.patch(
            "src.checkers.gitchecker.git_ls_remote",
            return_value={
                "refs/tags/v2.0.0": "tag-object",
                "refs/tags/v2.0.0^{}": "new",
            },
        ):
            await GitChecker._check_has_new(data)
        self.assertEqual(data.new_version.tag, "v2.0.0")
        self.assertEqual(data.new_version.commit, "new")
        self.assertIn(data.State.BROKEN, data.state)
        self.assertNotIn(data.State.OUTDATED, data.state)

    async def test_equivalent_alias_preserves_same_tag_commit_repair(self):
        for scheme in ("loose", "semantic"):
            for constraints in ({}, {"!=": "1.0.0"}):
                with self.subTest(scheme=scheme, constraints=constraints):
                    data = ExternalGitRepo.from_source_impl(
                        source_path="test.git",
                        source={
                            "type": "git",
                            "url": "https://example.org/test.git",
                            "tag": "1.0.0",
                            "commit": "old",
                            "x-checker-data": {
                                "type": "git",
                                "version-scheme": scheme,
                                "versions": constraints,
                            },
                        },
                    )
                    with mock.patch(
                        "src.checkers.gitchecker.git_ls_remote",
                        return_value={
                            "refs/tags/0.9.0": "older",
                            "refs/tags/1.0.0": "tag-object",
                            "refs/tags/1.0.0^{}": "new",
                            "refs/tags/v1.0.0": "alias",
                        },
                    ):
                        await GitChecker._check_has_new(data)
                    if constraints:
                        self.assertIsNone(data.new_version)
                    else:
                        self.assertIsNotNone(data.new_version)
                        self.assertEqual(data.new_version.tag, "1.0.0")
                        self.assertEqual(data.new_version.commit, "new")
                        self.assertIn(data.State.BROKEN, data.state)
                    self.assertNotIn(data.State.OUTDATED, data.state)

    async def test_github_tag_release_notes(self):
        repo = "https://github.com/NoRiskClient/noriskclient-launcher"
        tag = "v0.6.30-beta.4"
        link = f"{repo}/releases/tag/{tag}"
        data = ExternalGitRepo.from_source_impl(
            source_path="test.yml",
            source={
                "type": "git",
                "url": repo,
                "tag": "v0.6.30-beta.2",
                "commit": "old",
                "x-checker-data": {
                    "type": "git",
                    "tag-pattern": r"^v(\d+\.\d+\.\d+-beta\.\d+)$",
                    "version-scheme": "semantic",
                },
            },
        )
        with (
            mock.patch(
                "src.checkers.gitchecker.git_ls_remote",
                return_value={f"refs/tags/{tag}": "new"},
            ),
            mock.patch.object(
                GitChecker,
                "_get_json",
                return_value={"tag_name": tag, "html_url": link},
            ),
        ):
            async with aiohttp.ClientSession() as session:
                await GitChecker(session).check(data)
        self.assertEqual(SourceUpdate.from_source(data).release_url, link)

        for response in (
            {"message": "Not Found"},
            {"tag_name": "v0.6.30-beta.3", "html_url": link},
            {
                "tag_name": tag,
                "html_url": "https://github.com/other/repo/releases/tag/x",
            },
            CheckerQueryError("Rate limited"),
            CheckerFetchError("Blocked by robots.txt"),
        ):
            with self.subTest(response=response):
                with (
                    mock.patch(
                        "src.checkers.gitchecker.git_ls_remote",
                        return_value={f"refs/tags/{tag}": "new"},
                    ),
                    mock.patch.object(
                        GitChecker,
                        "_get_json",
                        side_effect=response
                        if isinstance(response, Exception)
                        else None,
                        return_value=response,
                    ),
                ):
                    async with aiohttp.ClientSession() as session:
                        await GitChecker(session).check(data)
                self.assertIsNone(SourceUpdate.from_source(data).release_url)
                self.assertEqual(data.new_version.tag, tag)

    def setUp(self):
        init_logging()
        self.robots_patcher = mock.patch(
            "src.lib.robots.RobotsCache.ensure_allowed", new_callable=mock.AsyncMock
        )
        self.robots_patcher.start()
        self.addCleanup(self.robots_patcher.stop)

    def test_sort_tags(self):
        t1 = TagWithVersion("x1", "v1.1", False, "1.1")
        t1a = TagWithVersion("x2", "v1.1", True, "1.1")
        t2 = TagWithVersion("y1", "v1.1.1", False, "1.1.1")
        t3 = TagWithVersion("z1", "v1.2", False, "1.2")
        t3a = TagWithVersion("z2", "v1.2", True, "1.2")
        self.assertTrue(t1a <= t1 < t3 and t3 >= t3a > t1)
        sorted_tags = [t1a, t1, t2, t3a, t3]
        shaked_tags = [t1, t1a, t3, t3a, t2]
        self.assertEqual(sorted(shaked_tags), sorted_tags)
        self.assertEqual(sorted(shaked_tags, reverse=True), sorted_tags[::-1])

    def test_sort_tags_semver(self):
        ts1 = TagWithSemver("x1", "v0.3", False, "0.3.0")
        ts1a = TagWithSemver("x1", "v0.3", True, "0.3.0")
        ts2 = TagWithSemver("x1", "v0.3.1", False, "0.3.1")
        ts2a = TagWithSemver("x1", "v0.3.1", True, "0.3.1")
        ts3 = TagWithSemver("x1", "v0.4.0-beta.1", False, "0.4.0-beta.1")
        ts3a = TagWithSemver("x1", "v0.4.0-beta.1", True, "0.4.0-beta.1")
        ts4 = TagWithSemver("x1", "v0.4.0", False, "0.4.0")
        ts4a = TagWithSemver("x1", "v0.4.0", True, "0.4.0")
        self.assertTrue(ts1a <= ts1 < ts3 and ts3 >= ts3a > ts1)
        sorted_tags_sem = [ts1a, ts1, ts2a, ts2, ts3a, ts3, ts4a, ts4]
        shaked_tags_sem = [ts2, ts1, ts4, ts1a, ts4a, ts3, ts3a, ts2a]
        self.assertEqual(sorted(shaked_tags_sem), sorted_tags_sem)
        self.assertEqual(sorted(shaked_tags_sem, reverse=True), sorted_tags_sem[::-1])

    @upstream_test
    async def test_check_and_update(self):
        checker = ManifestChecker(TEST_MANIFEST)
        ext_data = await checker.check()

        self.assertEqual(len(ext_data), 10)
        for data in ext_data:
            self.assertIsInstance(data, ExternalGitRepo)
            self.assertIsInstance(data.current_version, ExternalGitRef)
            if data.filename == "jansson.git":
                self.assertEqual(data.state, data.State.UNKNOWN)
                self.assertIsNone(data.new_version)
            elif data.filename == "c-vtapi.git":
                self.assertEqual(data.state, data.State.BROKEN)
                self.assertIsNotNone(data.new_version)
                self.assertEqual(data.current_version.url, data.new_version.url)
                self.assertNotEqual(
                    data.current_version.commit, data.new_version.commit
                )
                self.assertFalse(data.current_version.matches(data.new_version))
            elif data.filename == "qt-virustotal-uploader.git":
                self.assertIn(data.State.VALID, data.state)
                self.assertIsNone(data.new_version)
            elif data.filename == "protobuf-c.git":
                self.assertEqual(data.state, data.State.UNKNOWN)
                self.assertIsNone(data.new_version)
            elif data.filename == "yara.git":
                self.assertIn(data.State.BROKEN, data.state)
                self.assertIsNone(data.new_version)
            elif data.filename == "yara-python.git":
                self.assertEqual(data.state, data.State.UNKNOWN)
                self.assertIsNone(data.new_version)
            elif data.filename == "vt-py.git":
                self.assertIn(data.State.VALID, data.state)
                self.assertIsNone(data.new_version)
            elif data.filename == "extra-cmake-modules.git":
                self.assertIsNotNone(data.new_version)
                self.assertIsNone(data.new_version.branch)
                self.assertIsNotNone(data.new_version.commit)
                self.assertIsNotNone(data.new_version.tag)
                self.assertIsNotNone(data.new_version.version)
                self.assertNotEqual(data.new_version.tag, data.current_version.tag)
                self.assertNotEqual(
                    data.new_version.commit, data.current_version.commit
                )
                self.assertRegex(data.new_version.tag, r"^[vV][\d.]+$")
                self.assertRegex(data.new_version.version, r"^[\d.]+$")
            elif data.filename == "bluez-qt.git":
                self.assertEqual(data.new_version.tag, "v5.90.0")
            elif data.filename == "easyeffects.git":
                self.assertEqual(data.new_version.tag, "v4.8.5")
            else:
                self.fail(f"Unknown data {data.filename}")
            self._test_update_data(data, copy.deepcopy(data.source))

    def _test_update_data(self, data, orig_source):
        data.update()
        if data.filename == "qt-virustotal-uploader.git":
            self.assertEqual(data.source, orig_source)
        if (
            data.filename == "protobuf-c.git"
            or data.filename == "yara.git"
            or data.filename == "yara-python.git"
            or data.filename == "jansson.git"
        ):
            self.assertEqual(data.source, orig_source)
        elif data.filename == "c-vtapi.git":
            self.assertNotEqual(data.source, orig_source)
            self.assertEqual(data.source.keys(), orig_source.keys())
            self.assertIn("commit", data.source)
            self.assertNotIn("tag", data.source)
            self.assertIn("branch", data.source)
            self.assertEqual(data.source["commit"], data.new_version.commit)
        elif data.filename == "vt-py.git":
            self.assertEqual(data.source, orig_source)
        elif data.filename == "extra-cmake-modules.git":
            self.assertNotEqual(data.source, orig_source)
            self.assertIn("tag", data.source)
            self.assertIn("commit", data.source)
            self.assertNotEqual(data.source["commit"], orig_source["commit"])
            self.assertNotEqual(data.source["tag"], orig_source["tag"])

    async def test_version_scheme_validation(self):
        mock_refs = {
            "refs/tags/v1.0.0": "commit1",
            "refs/tags/v2.1.0": "commit2",
            "refs/tags/v21.0": "commit3",  # Invalid semver
            "refs/tags/v21.1": "commit4",  # Invalid semver
            "refs/tags/v3.0.0": "commit5",
            "refs/tags/v2.6.1rc1": "commit6",  # Should not match pattern
        }

        external_data = ExternalGitRepo.from_source_impl(
            source_path="test.git",
            source={
                "type": "git",
                "url": "https://example.com/test.git",
                "tag": "v1.0.0",
                "commit": "commit1",
                "x-checker-data": {
                    "type": "git",
                    "tag-pattern": r"^v([\d.]+)$",
                    "version-scheme": "semantic",
                },
            },
        )

        with mock.patch(
            "src.checkers.gitchecker.git_ls_remote",
            new_callable=mock.AsyncMock,
            return_value=mock_refs,
        ):
            async with aiohttp.ClientSession() as session:
                checker = GitChecker(session)
                await checker.check(external_data)

        self.assertIsNotNone(external_data.new_version)
        self.assertEqual(external_data.new_version.tag, "v3.0.0")
        self.assertEqual(external_data.new_version.version, "3.0.0")
        self.assertEqual(external_data.new_version.commit, "commit5")
        self.assertEqual(external_data.current_version.version, "1.0.0")
        self.assertEqual(external_data.current_version.commit, "commit1")

    async def test_default_tag_pattern(self):
        mock_refs = {
            "refs/tags/v1.0": "commit1",
            "refs/tags/V2.3.4": "commit2",
            "refs/tags/notaversion": "commit3",
            "refs/tags/v3.0.0": "commit4",
        }

        external_data = ExternalGitRepo.from_source_impl(
            source_path="test.git",
            source={
                "type": "git",
                "url": "https://example.com/test.git",
                "tag": "v1.0",
                "commit": "commit1",
                "x-checker-data": {"type": "git"},
            },
        )

        with mock.patch(
            "src.checkers.gitchecker.git_ls_remote",
            new_callable=mock.AsyncMock,
            return_value=mock_refs,
        ):
            async with aiohttp.ClientSession() as session:
                checker = GitChecker(session)
                await checker.check(external_data)

        self.assertIsNotNone(external_data.new_version)
        self.assertEqual(external_data.new_version.tag, "v3.0.0")
        self.assertEqual(external_data.new_version.version, "3.0.0")
        self.assertEqual(external_data.new_version.commit, "commit4")

    async def test_no_matching_tags_raises(self):
        mock_refs = {
            "refs/tags/notaversion": "commit1",
            "refs/tags/also-not": "commit2",
        }

        external_data = ExternalGitRepo.from_source_impl(
            source_path="test.git",
            source={
                "type": "git",
                "url": "https://example.com/test.git",
                "tag": "v1.0.0",
                "commit": "commit1",
                "x-checker-data": {
                    "type": "git",
                    "tag-pattern": r"^v([\d.]+)$",
                },
            },
        )

        with mock.patch(
            "src.checkers.gitchecker.git_ls_remote",
            new_callable=mock.AsyncMock,
            return_value=mock_refs,
        ):
            async with aiohttp.ClientSession() as session:
                checker = GitChecker(session)
                with self.assertRaises(CheckerQueryError) as ctx:
                    await checker.check(external_data)

        self.assertIn("https://example.com/test.git", str(ctx.exception))
        self.assertIn(r"^v([\d.]+)$", str(ctx.exception))

    async def test_exclude_versions(self):
        cases = [
            {
                "name": "single_exclude",
                "mock_refs": {
                    "refs/tags/v1.0.0": "commit1",
                    "refs/tags/v2.0.0": "commit2",
                    "refs/tags/v3.0.0": "commit3",
                },
                "versions": {"!=": "3.0.0"},
                "expected": ("v2.0.0", "2.0.0", "commit2"),
            },
            {
                "name": "multi_exclude",
                "mock_refs": {
                    "refs/tags/v1.0.0": "commit1",
                    "refs/tags/v2.0.0": "commit2",
                    "refs/tags/v2.1.0": "commit3",
                    "refs/tags/v3.0.0": "commit4",
                },
                "versions": {"!=": ["2.0.0", "2.1.0"]},
                "expected": ("v3.0.0", "3.0.0", "commit4"),
            },
        ]

        for case in cases:
            with self.subTest(case=case["name"]):
                external_data = ExternalGitRepo.from_source_impl(
                    source_path="test.git",
                    source={
                        "type": "git",
                        "url": "https://example.com/test.git",
                        "tag": "v1.0.0",
                        "commit": "commit1",
                        "x-checker-data": {
                            "type": "git",
                            "tag-pattern": r"^v([\d.]+)$",
                            "versions": case["versions"],
                        },
                    },
                )

                with mock.patch(
                    "src.checkers.gitchecker.git_ls_remote",
                    new_callable=mock.AsyncMock,
                    return_value=case["mock_refs"],
                ):
                    async with aiohttp.ClientSession() as session:
                        checker = GitChecker(session)
                        await checker.check(external_data)

                self.assertIsNotNone(external_data.new_version)
                tag, version, commit = case["expected"]
                self.assertEqual(external_data.new_version.tag, tag)
                self.assertEqual(external_data.new_version.version, version)
                self.assertEqual(external_data.new_version.commit, commit)


if __name__ == "__main__":
    unittest.main()
