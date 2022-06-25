import unittest
import json
import os
import random
import string
import tempfile

from src.manifest import ManifestChecker
from src.lib.externaldata import ExternalGitRef
from src.lib.errors import ManifestLoadError


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

# pylint: disable=protected-access
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
