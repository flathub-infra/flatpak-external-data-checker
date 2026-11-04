import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src import main
from src.checkers import Checker
from src.lib.checksums import MultiDigest
from src.lib.errors import (
    CheckerFetchError,
    CheckerQueryError,
    ManifestLoadError,
    UpdateGroupError,
)
from src.lib.externaldata import ExternalFile, ExternalGitRepo
from src.lint import ManifestLinter
from src.manifest import ManifestChecker


class TestUpdateGroups(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "org.example.App.json"
        self.appdata = self.root / "org.example.App.metainfo.xml"
        self.appdata.write_text(
            '<component type="desktop-application"><id>org.example.App</id>'
            '<releases><release version="1" date="2026-01-01"/></releases>'
            "</component>"
        )
        self.payloads = {}
        self.download_errors = {}

    def source(self, name, group="app", version="2", current="1"):
        metadata = {
            "type": "json",
            "url": f"https://example.invalid/{name}.json",
            "version-query": ".version",
            "url-query": ".url",
        }
        if group is not None:
            metadata["update-group"] = group
        self.payloads[metadata["url"]] = {
            "version": version,
            "url": f"https://example.invalid/{name}-{version}.tar.gz",
        }
        return {
            "type": "archive",
            "url": f"https://example.invalid/{name}-{current}.tar.gz",
            "sha256": "a" * 64,
            "x-checker-data": metadata,
        }

    def write(self, sources):
        self.path.write_text(
            json.dumps(
                {
                    "id": "org.example.App",
                    "modules": [{"name": "app", "sources": sources}],
                }
            )
        )

    async def check(self, checker, filter_type=None):
        await self.offline(checker.check(filter_type))

    async def offline(self, operation):
        async def get_json(_checker, url, **kwargs):
            payload = self.payloads[str(url)]
            if isinstance(payload, Exception):
                raise payload
            return payload

        async def download(url, **kwargs):
            if url in self.download_errors:
                raise self.download_errors[url]
            return ExternalFile(
                url=url,
                checksum=MultiDigest(sha256="b" * 64),
                size=100,
                version=None,
                timestamp=None,
            )

        with (
            patch.object(Checker, "_get_json", get_json),
            patch("src.checkers.utils.get_extra_data_info_from_url", download),
        ):
            return await operation

    async def test_matching_architectures_update_and_add_one_release(self):
        sources = [self.source("amd64"), self.source("arm64")]
        for source, arch in zip(sources, ("x86_64", "aarch64"), strict=True):
            source["only-arches"] = [arch]
        sources[0]["x-checker-data"]["is-main-source"] = True
        self.write(sources)
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        self.assertEqual(checker.get_errors(), [])
        self.assertEqual(len(checker.update_manifests()), 2)
        self.assertEqual(len(checker.source_updates), 2)
        self.assertEqual(self.appdata.read_text().count('version="2"'), 1)
        updated = json.loads(self.path.read_text())["modules"][0]["sources"]
        self.assertTrue(all(s["url"].endswith("-2.tar.gz") for s in updated))

    async def test_mismatch_is_atomic_across_files_but_unrelated_updates(self):
        first = self.source("amd64")
        first["x-checker-data"]["is-main-source"] = True
        second = self.source("arm64", version="1")
        external = self.root / "arm64.json"
        external.write_text(json.dumps(second))
        self.write([first, "arm64.json", self.source("dependency", group=None)])
        checker = ManifestChecker(str(self.path))
        members = [
            d for d in checker.get_external_data() if d.checker_data.get("update-group")
        ]
        before = copy.deepcopy([d.source for d in members])
        external_before = external.read_bytes()
        appdata_before = self.appdata.read_bytes()
        await self.check(checker)
        self.assertEqual(len(checker.get_errors(UpdateGroupError)), 1)
        self.assertEqual(len(checker.update_manifests()), 1)
        self.assertEqual(len(checker.get_errors(UpdateGroupError)), 1)
        self.assertEqual([d.source for d in members], before)
        self.assertEqual(external.read_bytes(), external_before)
        self.assertEqual(self.appdata.read_bytes(), appdata_before)
        self.assertEqual(len(checker.source_updates), 1)
        self.assertIn("dependency", checker.source_updates[0].source)

    async def test_unchanged_member_confirms_matching_selected_version(self):
        sources = [self.source("amd64"), self.source("arm64", current="2")]
        sources[1]["sha256"] = "b" * 64
        self.write(sources)
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        unchanged = checker.get_external_data()[1]
        self.assertIsNone(unchanged.new_version)
        self.assertEqual(unchanged.selected_version, "2")
        self.assertEqual(len(checker.update_manifests()), 1)
        self.assertEqual(checker.get_errors(), [])

    async def test_partial_write_selection_blocks_group_but_not_unrelated_source(self):
        self.write([self.source("a"), self.source("b"), self.source("dep", group=None)])
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        first, second, dependency = checker.get_external_data()
        before = copy.deepcopy([first.source, second.source])
        original = self.path.read_bytes()
        allowed = [first, dependency]
        self.assertEqual(checker.get_planned_updates(allowed), [dependency])
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(len(checker.update_manifests(allowed)), 1)
        self.assertEqual([first.source, second.source], before)
        self.assertEqual(checker.get_applied_external_data(), [dependency])
        self.assertEqual(len(checker.source_updates), 1)
        self.assertEqual(len(checker.get_errors(UpdateGroupError)), 1)

    async def test_write_selection_can_exclude_a_whole_group(self):
        self.write([self.source("a"), self.source("b"), self.source("dep", group=None)])
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        first, second, dependency = checker.get_external_data()
        before = copy.deepcopy([first.source, second.source])
        self.assertEqual(checker.get_planned_updates([dependency]), [dependency])
        self.assertEqual(len(checker.update_manifests([dependency])), 1)
        self.assertEqual([first.source, second.source], before)
        self.assertEqual(checker.get_applied_external_data(), [dependency])
        self.assertEqual(
            checker.get_outdated_external_data(include_blocked=False), [dependency]
        )
        self.assertEqual(checker.get_errors(), [])

    async def test_write_selection_can_omit_unchanged_member(self):
        sources = [self.source("a"), self.source("b", current="2")]
        sources[1]["sha256"] = "b" * 64
        self.write(sources)
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        first, second = checker.get_external_data()
        self.assertIsNone(second.new_version)
        self.assertEqual(checker.get_planned_updates([first]), [first])
        self.assertEqual(len(checker.update_manifests([first])), 1)
        self.assertEqual(checker.get_errors(), [])

    async def test_mutable_url_member_updates_its_checksum(self):
        sources = [self.source("amd64"), self.source("arm64")]
        sources[1]["url"] = "https://example.invalid/arm64-latest.tar.gz"
        self.payloads[sources[1]["x-checker-data"]["url"]]["url"] = sources[1]["url"]
        self.write(sources)
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        self.assertEqual(checker.get_errors(), [])
        self.assertEqual(len(checker.update_manifests()), 2)
        updated = json.loads(self.path.read_text())["modules"][0]["sources"]
        self.assertTrue(all(s["sha256"] == "b" * 64 for s in updated))
        self.assertEqual(updated[1]["url"], sources[1]["url"])

    async def test_unchanged_url_download_failure_blocks_the_group(self):
        sources = [self.source("amd64"), self.source("arm64", current="2")]
        self.download_errors[sources[1]["url"]] = CheckerFetchError("download failed")
        self.write(sources)
        checker = ManifestChecker(str(self.path))
        original = self.path.read_bytes()
        before = copy.deepcopy([data.source for data in checker.get_external_data()])
        await self.check(checker)
        self.assertEqual(len(checker.get_errors(CheckerFetchError)), 1)
        self.assertEqual(len(checker.get_errors(UpdateGroupError)), 1)
        self.assertEqual(checker.update_manifests(), [])
        self.assertEqual([data.source for data in checker.get_external_data()], before)
        self.assertEqual(self.path.read_bytes(), original)

    async def test_all_unchanged_is_not_an_error_or_a_write(self):
        sources = [self.source("a", current="2"), self.source("b", current="2")]
        for source in sources:
            source["sha256"] = "b" * 64
        self.write(sources)
        before = self.path.read_bytes()
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        self.assertEqual(checker.update_manifests(), [])
        self.assertEqual(checker.get_errors(), [])
        self.assertEqual(self.path.read_bytes(), before)

    async def test_failure_unknown_absent_and_skipped_members_block(self):
        for outcome in ("failure", "absent", "unknown", "skipped", "partial-failure"):
            with self.subTest(outcome=outcome):
                self.write([self.source("a"), self.source("b")])
                checker = ManifestChecker(str(self.path))
                second = checker.get_external_data()[1]
                if outcome == "failure":
                    self.payloads[second.checker_data["url"]] = CheckerQueryError(
                        "offline failure"
                    )
                elif outcome == "absent":
                    self.payloads[second.checker_data["url"]] = {
                        "version": None,
                        "url": None,
                    }
                elif outcome == "skipped":
                    second.checker_data["type"] = "not-a-checker"
                before = self.path.read_bytes()
                await self.check(checker)
                if outcome in ("unknown", "partial-failure"):
                    second.new_version = second.current_version._replace(
                        url="https://example.invalid/new", version=None
                    )
                    if outcome == "partial-failure":
                        second.new_version = second.new_version._replace(version="2")
                        second.check_failed = True
                self.assertEqual(checker.update_manifests(), [])
                self.assertEqual(len(checker.get_errors(UpdateGroupError)), 1)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(checker.source_updates, [])

    async def test_blocked_important_source_does_not_open_gate(self):
        first = self.source("a")
        first["x-checker-data"]["is-main-source"] = True
        self.write(
            [first, self.source("b", version="1"), self.source("dep", group=None)]
        )
        checker = ManifestChecker(str(self.path))
        checker.opts = main.manifest.CheckerOptions(require_important_update=True)
        await self.check(checker)
        before = self.path.read_bytes()
        self.assertEqual(checker.update_manifests(), [])
        self.assertEqual(self.path.read_bytes(), before)

    async def test_ungrouped_architectures_remain_independent(self):
        sources = [
            self.source("a", group=None),
            self.source("b", group=None, version="1"),
        ]
        for source, arch in zip(sources, ("x86_64", "aarch64"), strict=True):
            source["only-arches"] = [arch]
        self.write(sources)
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        self.assertEqual(len(checker.update_manifests()), 1)
        self.assertEqual(checker.get_errors(), [])

    async def test_separate_group_is_not_blocked(self):
        self.write(
            [
                self.source("a"),
                self.source("b", version="1"),
                self.source("c", group="other"),
                self.source("d", group="other"),
            ]
        )
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        self.assertEqual(len(checker.update_manifests()), 2)
        self.assertEqual(len(checker.source_updates), 2)

    async def test_parent_child_checks_are_not_an_implicit_group(self):
        parent = self.source("app", group=None)
        parent["x-checker-data"]["source-id"] = "app"
        child = self.source("patch", group=None, version="1")
        child["x-checker-data"].update(
            {
                "parent-id": "app",
                "version-query": '$parent.new.version | sub("2"; "1")',
                "url-query": '"https://example.invalid/patch-" + $version + ".tar.gz"',
            }
        )
        self.write([parent, child])
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        self.assertEqual(len(checker.update_manifests()), 1)
        self.assertEqual(checker.get_errors(), [])

    async def test_parent_child_mismatch_is_blocked_when_explicitly_grouped(self):
        parent = self.source("app")
        parent["x-checker-data"]["source-id"] = "app"
        child = self.source("patch", version="1")
        child["x-checker-data"]["parent-id"] = "app"
        self.write([parent, child])
        checker = ManifestChecker(str(self.path))
        before = copy.deepcopy([d.source for d in checker.get_external_data()])
        await self.check(checker)
        self.assertEqual(checker.update_manifests(), [])
        self.assertEqual([d.source for d in checker.get_external_data()], before)
        self.assertEqual(len(checker.get_errors(UpdateGroupError)), 1)

    async def test_file_and_git_patch_group(self):
        parent = self.source("app")
        parent["x-checker-data"]["source-id"] = "app"
        child = {
            "type": "git",
            "url": "https://example.invalid/patch.git",
            "tag": "v1",
            "commit": "a" * 40,
            "x-checker-data": {
                "type": "json",
                "update-group": "app",
                "parent-id": "app",
                "version-query": "$parent.new.version",
                "tag-query": '"v" + $parent.new.version',
                "commit-query": '"' + "b" * 40 + '"',
            },
        }
        self.write([parent, child])
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        self.assertEqual(checker.get_errors(), [])
        self.assertEqual(len(checker.update_manifests()), 2)
        updated = json.loads(self.path.read_text())["modules"][0]["sources"][1]
        self.assertEqual(updated["tag"], "v2")

    async def test_filter_leaves_group_member_unchecked_and_blocks(self):
        self.write([self.source("a"), self.source("b")])
        checker = ManifestChecker(str(self.path))
        await self.check(checker, ExternalGitRepo.Type.GIT)
        self.assertEqual(len(checker.get_errors(UpdateGroupError)), 1)
        self.assertEqual(checker.update_manifests(), [])

    async def test_filter_does_not_wait_forever_on_excluded_parent(self):
        parent = self.source("app")
        parent["x-checker-data"]["source-id"] = "app"
        child = {
            "type": "git",
            "url": "https://example.invalid/patch.git",
            "tag": "v1",
            "commit": "a" * 40,
            "x-checker-data": {
                "type": "json",
                "update-group": "app",
                "parent-id": "app",
                "version-query": "$parent.new.version",
                "tag-query": '"v" + $parent.new.version',
            },
        }
        self.write([parent, child])
        checker = ManifestChecker(str(self.path))
        await asyncio.wait_for(self.check(checker, ExternalGitRepo.Type.GIT), timeout=5)
        self.assertEqual(len(checker.get_errors(UpdateGroupError)), 1)
        self.assertEqual(checker.update_manifests(), [])

    async def test_exact_version_comparison(self):
        for version in ("2.0", "v2", "2 "):
            with self.subTest(version=version):
                self.write([self.source("a"), self.source("b", version=version)])
                checker = ManifestChecker(str(self.path))
                await self.check(checker)
                self.assertEqual(checker.update_manifests(), [])
                self.assertEqual(len(checker.get_errors(UpdateGroupError)), 1)

    async def test_unchanged_digest_candidate_retains_selected_version(self):
        self.write([self.source("a"), self.source("b", current="2")])
        contents = json.loads(self.path.read_text())
        second = contents["modules"][0]["sources"][1]
        second.update({"type": "extra-data", "sha256": "b" * 64, "size": 100})
        self.path.write_text(json.dumps(contents))
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        unchanged = checker.get_external_data()[1]
        self.assertIsNone(unchanged.new_version)
        self.assertEqual(unchanged.selected_version, "2")
        self.assertEqual(len(checker.update_manifests()), 1)
        self.assertEqual(checker.get_errors(), [])

    async def test_unchanged_rotating_url_confirms_selected_version(self):
        sources = [self.source("a"), self.source("b", current="2")]
        sources[1]["sha256"] = "b" * 64
        sources[1]["x-checker-data"] = {
            "type": "rotating-url",
            "url": sources[1]["url"],
            "pattern": r"https://example.invalid/b-([0-9]+)\.tar\.gz",
            "update-group": "app",
        }
        self.write(sources)
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        self.assertEqual(checker.get_external_data()[1].selected_version, "2")
        self.assertIsNone(checker.get_external_data()[1].new_version)
        self.assertEqual(checker.get_errors(), [])
        self.assertEqual(len(checker.update_manifests()), 1)

    def test_unreadable_includes_do_not_hide_group_members(self):
        for missing in ("module", "oversized-source"):
            with self.subTest(missing=missing):
                self.write([self.source("a"), self.source("b")])
                contents = json.loads(self.path.read_text())
                if missing == "module":
                    contents["modules"].append("missing-module.json")
                else:
                    large = self.root / "large.json"
                    large.write_text(" " * (1024 * 1024 + 1))
                    contents["modules"][0]["sources"].append(large.name)
                self.path.write_text(json.dumps(contents))
                with self.assertRaisesRegex(ManifestLoadError, "incomplete manifest"):
                    ManifestChecker(str(self.path))

    async def test_blocked_broken_source_does_not_force_automerge(self):
        first = self.source("a")
        first.update({"type": "extra-data", "size": 100})
        self.write(
            [first, self.source("b", version="1"), self.source("dep", group=None)]
        )
        checker = ManifestChecker(str(self.path))
        await self.check(checker)
        first_data = checker.get_external_data()[0]
        first_data.state |= first_data.State.BROKEN
        self.assertEqual(len(checker.update_manifests()), 1)
        pr = MagicMock(state="open", mergeable=True)
        pr.head.repo.get_commit.return_value.get_combined_status.return_value.state = (
            "success"
        )
        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
            patch("src.main.Github") as github,
            patch(
                "src.main.subprocess.check_output",
                return_value=b"https://github.com/o/r",
            ),
            patch("src.main.subprocess.run") as push,
            patch("builtins.open", side_effect=FileNotFoundError),
        ):
            github.return_value.get_repo.return_value.get_pulls.return_value = [pr]
            change = main.CommittedChanges(
                "Update dep", None, "abc", "update", "master"
            )
            self.assertTrue(main.open_pr(change, manifest_checker=checker))
        pr.merge.assert_not_called()
        pr.create_issue_comment.assert_not_called()
        push.assert_not_called()

    def test_lint_group_metadata_and_membership(self):
        for group in ("", "  ", None, 42, ["app"]):
            with self.subTest(group=group):
                sources = [self.source("a"), self.source("b")]
                sources[1]["x-checker-data"]["update-group"] = group
                self.write(sources)
                errors = ManifestLinter(1024 * 1024).lint(self.path)
                self.assertTrue(errors)
                self.assertIn("update-group", "\n".join(errors))
        self.write([self.source("a")])
        self.assertIn(
            "at least two", "\n".join(ManifestLinter(1024 * 1024).lint(self.path))
        )
        self.write([self.source("a"), self.source("b")])
        self.assertEqual(ManifestLinter(1024 * 1024).lint(self.path), [])

    def test_unloadable_member_cannot_be_silently_omitted(self):
        for change in (
            {"type": "patch"},
            {"type": "extra-data", "sha512": "b"},
            {"url": None},
        ):
            with self.subTest(change=change):
                bad = self.source("c") | change
                if bad["type"] == "extra-data":
                    del bad["sha256"]
                self.write([self.source("a"), self.source("b"), bad])
                with self.assertRaisesRegex(ManifestLoadError, "update-group member"):
                    ManifestChecker(str(self.path))

    async def test_cli_edit_only_reports_blocked_group_without_writing(self):
        sources = [self.source("a"), self.source("b", version="1")]
        sources[1]["sha256"] = "b" * 64
        self.write(sources)
        before = self.path.read_bytes()
        args = main.parse_cli_args(["--edit-only", "--check-outdated", str(self.path)])
        with patch("src.main.init_logging"):
            outdated, errors, updated = await self.offline(main.run_with_args(args))
        self.assertEqual(outdated, 1)
        self.assertEqual(errors, 1)
        self.assertFalse(updated)
        self.assertEqual(self.path.read_bytes(), before)
