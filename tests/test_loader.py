import unittest
import json
import os
import random
import string
import tempfile

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
                },
                {
                    "type": "file",
                    "url": "http://example.com/first-one.txt",
                    "sha256": "x",
                },
                {
                    "type": "file",
                    "url": "http://example.com/first-two.txt",
                    "sha256": "x",
                },
            ],
        },
        {
            "name": "second",
            "sources": [
                {
                    "type": "git",
                    "url": "http://example.com/second-repo.git",
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

        self.assertEqual(modules[1].name, "second")
        self.assertIsNone(modules[1].parent)
        self.assertEqual(
            [s.filename for s in modules[1].sources],
            ["second-repo.git"],
        )

        self.assertEqual(modules[2].name, "first-child")
        self.assertIs(modules[2].parent, modules[1])
        self.assertEqual(
            [s.filename for s in modules[2].sources],
            ["first-child-repo.git", "first-child.txt"],
        )

        self.assertEqual(modules[3].name, "first-grandchild")
        self.assertIs(modules[3].parent, modules[2])
        self.assertEqual(
            [s.filename for s in modules[3].sources],
            ["first-grandchild.tar"],
        )

        self.assertEqual(modules[4].name, "second-child")
        self.assertIs(modules[4].parent, modules[1])
        self.assertEqual(
            [s.filename for s in modules[4].sources],
            ["second-child.txt"],
        )

        self.assertEqual(modules[5].name, "third")
        self.assertIsNone(modules[5].parent)
        self.assertEqual(
            [s.filename for s in modules[5].sources],
            ["third.tar"],
        )
        # fmt: on
