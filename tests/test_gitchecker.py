import copy
import os
import unittest
import aiohttp
from unittest import mock

from src.manifest import ManifestChecker
from src.lib.externaldata import ExternalGitRepo, ExternalGitRef
from src.lib.utils import init_logging
from src.checkers.gitchecker import TagWithVersion, TagWithSemver, GitChecker

TEST_MANIFEST = os.path.join(os.path.dirname(__file__), "com.virustotal.Uploader.yml")


class TestGitChecker(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        init_logging()

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
        if data.filename == "protobuf-c.git":
            self.assertEqual(data.source, orig_source)
        elif data.filename == "yara.git":
            self.assertEqual(data.source, orig_source)
        elif data.filename == "yara-python.git":
            self.assertEqual(data.source, orig_source)
        elif data.filename == "jansson.git":
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


if __name__ == "__main__":
    unittest.main()
