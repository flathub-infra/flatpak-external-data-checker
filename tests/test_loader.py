import json
import os
import random
import string
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.lib.checksums import MultiDigest
from src.lib.errors import ManifestLoadError, SourceLoadError
from src.lib.externaldata import (
    BuilderSource,
    ExternalBase,
    ExternalFile,
    ExternalGitRef,
    FileSource,
)
from src.manifest import ManifestChecker

TEST_MANIFEST_DATA = {
    "id": "fedc.test.Loader",
    "modules": [
        {
            "name": "first",
            "sources": [
                {
                    "type": "git",
                    "url": "http://example.com/first-repo.git",
                    "x-checker-data": {
                        "type": "dummy",
                        "parent-id": "my-custom-id",
                    },
                },
                {
                    "type": "file",
                    "url": "http://example.com/first-one.txt",
                    "sha256": "x",
                    "x-checker-data": {
                        "type": "dummy",
                        "source-id": "my-custom-id",
                    },
                },
                {
                    "type": "file",
                    "url": "http://example.com/first-two.txt",
                    "sha256": "x",
                    "x-checker-data": {
                        "type": "dummy",
                        "parent-id": "my-custom-id",
                    },
                },
            ],
        },
        {
            "name": "second",
            "sources": [
                {
                    "type": "git",
                    "url": "http://example.com/second-repo.git",
                    "x-checker-data": {
                        "type": "dummy",
                        "parent-id": "first-file-1",
                    },
                },
            ],
            "modules": [
                {
                    "name": "first-child",
                    "sources": [
                        {
                            "type": "git",
                            "url": "http://example.com/first-child-repo.git",
                        },
                        {
                            "type": "file",
                            "url": "http://example.com/first-child.txt",
                            "sha256": "x",
                        },
                    ],
                    "modules": [
                        {
                            "name": "first-grandchild",
                            "sources": [
                                {
                                    "type": "archive",
                                    "url": "http://example.com/first-grandchild.tar",
                                    "sha256": "x",
                                    "x-checker-data": {
                                        "type": "dummy",
                                        "parent-id": "first-child-file-0",
                                    },
                                },
                            ],
                        },
                    ],
                },
                {
                    "name": "second-child",
                    "sources": [
                        {
                            "type": "file",
                            "url": "http://example.com/second-child.txt",
                            "sha256": "x",
                        },
                        {
                            "type": "patch",
                            "path": "second-child.patch",
                        },
                    ],
                },
            ],
        },
        {
            "name": "third",
            "sources": [
                {
                    "type": "archive",
                    "url": "http://example.com/third.tar",
                    "sha256": "x",
                    "x-checker-data": {
                        "type": "dummy",
                        "parent-id": "second-git-0",
                    },
                },
            ],
        },
    ],
}
TEST_MANIFEST_INVALID_NO_ID = {
    "id": "fedc.test.Loader",
    "modules": [
        {
            "name": "broken",
            "sources": [
                {
                    "type": "git",
                    "url": "http://example.com/invalid.git",
                    "x-checker-data": {
                        "type": "dummy",
                        "parent-id": "no-such-id",
                    },
                },
            ],
        },
    ],
}
TEST_MANIFEST_INVALID_LOOP = {
    "id": "fedc.test.Loader",
    "modules": [
        {
            "name": "broken",
            "sources": [
                {
                    "type": "git",
                    "url": "http://example.com/first.git",
                    "x-checker-data": {
                        "type": "dummy",
                        "parent-id": "broken-git-1",
                    },
                },
                {
                    "type": "git",
                    "url": "http://example.com/second.git",
                    "x-checker-data": {
                        "type": "dummy",
                        "parent-id": "broken-git-0",
                    },
                },
            ],
        },
    ],
}


class TestManifestLoader(unittest.IsolatedAsyncioTestCase):
    test_dir: tempfile.TemporaryDirectory

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.test_dir.cleanup()

    def _load_manifest(self, manifest_data):
        rand = "".join(random.sample(string.ascii_letters + string.digits, 10))
        mf_path = os.path.join(self.test_dir.name, f"{rand}.json")

        with open(mf_path, "w") as mf:
            json.dump(manifest_data, mf)

        return ManifestChecker(mf_path)

    def test_same_version(self):
        def assertSame(data1, data2):
            self.assertTrue(data1.is_same_version(data2))
            self.assertTrue(data2.is_same_version(data1))

        def assertDiff(data1, data2):
            self.assertFalse(data1.is_same_version(data2))
            self.assertFalse(data2.is_same_version(data1))

        assertSame(
            ExternalGitRef("http://example.com", None, None, "a", "v1.0", None),
            ExternalGitRef("http://example.com", None, None, "b", "v1.0", None),
        )
        assertDiff(
            ExternalGitRef("http://example.com", None, None, "a", "v1.0", None),
            ExternalGitRef("http://example.com", None, None, "b", "v1.1", None),
        )
        assertDiff(
            ExternalGitRef("http://example.com", None, None, "a", "v1.0", None),
            ExternalGitRef("http://example.com", None, None, "a", "v1.1", None),
        )
        assertDiff(
            ExternalGitRef("http://example.com", None, None, "a", "v1.0", "same"),
            ExternalGitRef("http://example.com", None, None, "a", "v1.1", "same"),
        )
        assertDiff(
            ExternalGitRef("http://example.com", None, None, "a", "v1.0", "one"),
            ExternalGitRef("http://example.com", None, None, "a", "v1.0", "two"),
        )
        # No tag, same branch
        assertSame(
            ExternalGitRef("http://example.com", None, None, "a", None, "same"),
            ExternalGitRef("http://example.com", None, None, "b", None, "same"),
        )
        # No tag, different branches
        assertDiff(
            ExternalGitRef("http://example.com", None, None, "a", None, "one"),
            ExternalGitRef("http://example.com", None, None, "b", None, "two"),
        )
        # No tag or branch, same commit
        assertSame(
            ExternalGitRef("http://example.com", None, None, "a", None, None),
            ExternalGitRef("http://example.com", None, None, "a", None, None),
        )
        # No tag or branch, different commits
        assertDiff(
            ExternalGitRef("http://example.com", None, None, "a", None, None),
            ExternalGitRef("http://example.com", None, None, "b", None, None),
        )

    @staticmethod
    def _parent_source(ident, parent=None):
        metadata = {"type": "dummy"}
        if ident is not None:
            metadata["source-id"] = ident
        if parent is not None:
            metadata["parent-id"] = parent
        return {
            "type": "git",
            "url": f"https://example.com/{ident}.git",
            "x-checker-data": metadata,
        }

    def test_parent_lookup_first_duplicate_wins(self):
        sources = [
            self._parent_source("child", "duplicate-git-1"),
            self._parent_source(None),
            self._parent_source("duplicate-git-1", "other"),
            self._parent_source("other"),
            self._parent_source("later-child", "duplicate-git-1"),
        ]
        data = self._load_manifest(
            {"name": "duplicate", "sources": sources}
        ).get_external_data()
        self.assertIs(data[0].parent, data[1])
        self.assertIs(data[2].parent, data[3])
        self.assertIs(data[4].parent, data[1])

    def test_parent_lookup_does_not_evaluate_unused_ids(self):
        for sources in (
            [self._parent_source(None)],
            [
                self._parent_source("parent"),
                self._parent_source(None, "parent"),
                self._parent_source(None, "parent"),
            ],
        ):
            with self.subTest(sources=sources):
                data = self._load_manifest(sources).get_external_data()
                self.assertEqual(len(data), len(sources))
                for child in data[1:]:
                    self.assertIs(child.parent, data[0])

    def test_parent_lookup_preserves_id_errors(self):
        with self.assertRaisesRegex(SourceLoadError, "Can't get source id"):
            self._load_manifest(
                [
                    self._parent_source(None),
                    self._parent_source("child", "parent"),
                    self._parent_source("parent"),
                ]
            )

    def test_parent_lookup_missing_id(self):
        with self.assertRaisesRegex(
            ManifestLoadError,
            'Source git child.git: parent source with ID "missing" not found',
        ):
            self._load_manifest([self._parent_source("child", "missing")])

    def test_parent_loops_and_error_order(self):
        for edges, culprit in (
            ([("self", "self")], "self"),
            ([("a", "b"), ("b", "c"), ("c", "a")], "c"),
            ([("tail", "a"), ("a", "b"), ("b", "a")], "b"),
        ):
            with self.subTest(edges=edges):
                # A later missing parent must not mask an earlier loop.
                sources = [self._parent_source(*edge) for edge in edges]
                sources.append(self._parent_source("missing", "absent"))
                with self.assertRaisesRegex(
                    ManifestLoadError,
                    f"Source git {culprit}.git: inheritance loop detected",
                ):
                    self._load_manifest(sources)

    def test_parent_chains_in_both_orders(self):
        sources = [self._parent_source("0")]
        sources.extend(self._parent_source(str(i), str(i - 1)) for i in range(1, 150))
        for ordered in (sources, sources[::-1]):
            with self.subTest(first=ordered[0]):
                data = self._load_manifest(ordered).get_external_data()
                by_id = {source.ident: source for source in data}
                self.assertIsNone(by_id["0"].parent)
                for i in range(1, len(data)):
                    self.assertIs(by_id[str(i)].parent, by_id[str(i - 1)])

    def test_parent_lookup_evaluates_each_id_once(self):
        sources = [self._parent_source(str(i), "parent") for i in range(100)]
        sources.append(self._parent_source("parent"))
        counted_ident = Mock(wraps=BuilderSource.ident.fget)
        with patch.object(BuilderSource, "ident", property(counted_ident)):
            data = self._load_manifest(sources).get_external_data()
        self.assertEqual(counted_ident.call_count, len(sources))
        for child in data[:-1]:
            self.assertIs(child.parent, data[-1])

    def test_load(self):
        manifest = self._load_manifest(TEST_MANIFEST_DATA)
        self.assertEqual(manifest.kind, manifest.Kind.APP)
        modules = sum(manifest._modules.values(), [])
        self.assertEqual(len(modules), 6)

        # fmt: off
        self.assertEqual(modules[0].name, "first")
        self.assertIsNone(modules[0].parent)
        self.assertEqual(
            [s.filename for s in modules[0].sources],
            ["first-repo.git", "first-one.txt", "first-two.txt"],
        )
        self.assertEqual(
            [s.ident for s in modules[0].sources],
            ["first-git-0", "my-custom-id", "first-file-1"],
        )
        self.assertIs(modules[0].sources[0].parent, modules[0].sources[1])
        self.assertIsNone(modules[0].sources[1].parent)
        self.assertIs(modules[0].sources[2].parent, modules[0].sources[1])

        self.assertEqual(modules[1].name, "second")
        self.assertIsNone(modules[1].parent)
        self.assertEqual(
            [s.filename for s in modules[1].sources],
            ["second-repo.git"],
        )
        self.assertEqual(
            [s.ident for s in modules[1].sources],
            ["second-git-0"],
        )
        self.assertIs(modules[1].sources[0].parent, modules[0].sources[2])

        self.assertEqual(modules[2].name, "first-child")
        self.assertIs(modules[2].parent, modules[1])
        self.assertEqual(
            [s.filename for s in modules[2].sources],
            ["first-child-repo.git", "first-child.txt"],
        )
        self.assertEqual(
            [s.ident for s in modules[2].sources],
            ["first-child-git-0", "first-child-file-0"],
        )

        self.assertEqual(modules[3].name, "first-grandchild")
        self.assertIs(modules[3].parent, modules[2])
        self.assertEqual(
            [s.filename for s in modules[3].sources],
            ["first-grandchild.tar"],
        )
        self.assertEqual(
            [s.ident for s in modules[3].sources],
            ["first-grandchild-archive-0"],
        )
        self.assertIs(modules[3].sources[0].parent, modules[2].sources[1])

        self.assertEqual(modules[4].name, "second-child")
        self.assertIs(modules[4].parent, modules[1])
        self.assertEqual(
            [s.filename for s in modules[4].sources],
            ["second-child.txt"],
        )
        self.assertEqual(
            [s.ident for s in modules[4].sources],
            ["second-child-file-0"],
        )

        self.assertEqual(modules[5].name, "third")
        self.assertIsNone(modules[5].parent)
        self.assertEqual(
            [s.filename for s in modules[5].sources],
            ["third.tar"],
        )
        self.assertEqual(
            [s.ident for s in modules[5].sources],
            ["third-archive-0"],
        )
        self.assertIs(modules[5].sources[0].parent, modules[1].sources[0])
        # fmt: on

    def test_invalid_relations(self):
        with self.assertRaises(ManifestLoadError):
            self._load_manifest(TEST_MANIFEST_INVALID_NO_ID)
        with self.assertRaises(ManifestLoadError):
            self._load_manifest(TEST_MANIFEST_INVALID_LOOP)


class TestExternalData(unittest.IsolatedAsyncioTestCase):
    def test_schema_validation_error(self):
        with self.assertRaises(SourceLoadError):
            ExternalBase.from_source(
                "foo.json", {"type": 123, "url": "http://example.com"}
            )

    def test_missing_extra_data_checksum(self):
        with self.assertRaises(SourceLoadError):
            ExternalBase.from_source(
                "foo.json", {"type": "extra-data", "url": "http://example.com"}
            )

    def test_missing_file_checksum(self):
        with self.assertRaises(SourceLoadError):
            ExternalBase.from_source(
                "foo.json", {"type": "file", "url": "http://example.com"}
            )

    def test_ident_and_repr_without_module(self):
        source_dict = {
            "type": "file",
            "url": "http://example.com/file.txt",
            "sha256": "x",
        }
        source = FileSource.from_source("foo.json", source_dict, module=None)

        with self.assertRaises(SourceLoadError):
            _ = source.ident

        self.assertIn("FileSource file", BuilderSource.__repr__(source))

    def test_update_removes_size_for_non_extra_data(self):
        source_dict = {
            "type": "file",
            "url": "http://old.com/file",
            "sha256": "a" * 64,
            "size": 1024,
        }
        source = FileSource.from_source("foo.json", source_dict)
        source.new_version = ExternalFile(
            url="http://new.com/file",
            checksum=MultiDigest(sha256="b" * 64),
            size=2048,
            version=None,
            timestamp=None,
        )
        source.update()

        self.assertNotIn("size", source.source)

    def test_git_ref_lightweight_tag_match(self):
        ref = ExternalGitRef(
            url="http://repo",
            commit="lightweight",
            tag="v1",
            branch=None,
            version=None,
            timestamp=None,
        )
        refs = {
            "refs/tags/v1": "annotated",
            "refs/tags/v1^{}": "lightweight",
        }
        got = ref._get_tagged_commit(refs, "v1")

        self.assertEqual(got, "lightweight")

    @patch("src.lib.externaldata.utils.git_ls_remote")
    async def test_fetch_remote_head(self, mock_ls_remote):
        mock_ls_remote.return_value = {"HEAD": "head-commit"}
        ref = ExternalGitRef(
            url="http://repo",
            commit=None,
            tag=None,
            branch=None,
            version=None,
            timestamp=None,
        )
        new_ref = await ref.fetch_remote()

        self.assertEqual(new_ref.commit, "head-commit")


if __name__ == "__main__":
    unittest.main()
