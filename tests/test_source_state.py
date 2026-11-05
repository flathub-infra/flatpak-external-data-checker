import dataclasses
import unittest

from src.lib.checksums import MultiDigest
from src.lib.externaldata import (
    ArchiveSource,
    ExternalBase,
    ExternalData,
    ExternalFile,
    ExternalGitRef,
    ExternalGitRepo,
    ExtraDataSource,
    FileSource,
)


class TestSourceFactories(unittest.TestCase):
    def test_file_dispatch_and_state_types(self):
        for source_type, source_class in (
            ("file", FileSource),
            ("archive", ArchiveSource),
            ("extra-data", ExtraDataSource),
        ):
            with self.subTest(source_type=source_type):
                source = {
                    "type": source_type,
                    "url": "https://example.com/file",
                    "sha256": "abc",
                    "size": 123,
                }
                for factory in (ExternalBase, ExternalData, source_class):
                    data = factory.from_source("source.json", source)
                    self.assertIs(type(data), source_class)
                    self.assertIs(type(data.current_version), ExternalFile)
                    self.assertIsNone(data.new_version)
                    self.assertIs(data.source, source)

    def test_git_dispatch_and_state_types(self):
        source = {"type": "git", "url": "https://example.com/repo.git", "tag": "v1"}
        for factory in (ExternalBase, ExternalGitRepo):
            with self.subTest(factory=factory):
                data = factory.from_source("source.json", source)
                self.assertIs(type(data), ExternalGitRepo)
                self.assertIs(type(data.current_version), ExternalGitRef)
                self.assertIsNone(data.new_version)
                self.assertIs(data.source, source)

    def test_replace_preserves_dataclass_subclass(self):
        @dataclasses.dataclass(frozen=True)
        class CustomFile(ExternalFile):
            label: str

        original = CustomFile(
            url="https://example.com/file",
            version="1",
            timestamp=None,
            checksum=MultiDigest(sha256="abc"),
            size=123,
            label="old",
        )
        replaced = original._replace(version="2", label="new")
        self.assertIs(type(replaced), CustomFile)
        self.assertEqual(replaced.label, "new")
        self.assertEqual(replaced.version, "2")
        self.assertEqual(original.label, "old")
        self.assertEqual(original.version, "1")
        with self.assertRaises(TypeError):
            original._replace(unknown="field")


class TestGitSourceUpdates(unittest.TestCase):
    def test_ref_transitions(self):
        refs = ({}, {"tag": "v1"}, {"branch": "stable"})
        new_refs = ({}, {"tag": "v2"}, {"branch": "main"})
        for old_ref in refs:
            for new_ref in new_refs:
                with self.subTest(old=old_ref, new=new_ref):
                    source = {
                        "type": "git",
                        "url": "https://example.org/old.git",
                        "commit": "a" * 40,
                        "dest": "dependency",
                        "disable-submodules": True,
                        "x-checker-data": {"type": "git"},
                        **old_ref,
                    }
                    original = source.copy()
                    data = ExternalGitRepo.from_source("source.json", source)
                    current = data.current_version
                    data.new_version = current._replace(
                        url="https://example.org/new.git",
                        commit="b" * 40,
                        tag=new_ref.get("tag"),
                        branch=new_ref.get("branch"),
                    )
                    data.update()
                    expected = {
                        key: value
                        for key, value in original.items()
                        if key not in ("tag", "branch")
                    }
                    expected.update(
                        url=data.new_version.url, commit="b" * 40, **new_ref
                    )
                    self.assertEqual(source, expected)
                    self.assertIs(data.current_version, current)
                    reloaded = ExternalGitRepo.from_source("source.json", source)
                    self.assertTrue(reloaded.current_version.matches(data.new_version))
                    data.update()
                    self.assertEqual(source, expected)

    def test_no_new_version_leaves_source_unchanged(self):
        source = {
            "type": "git",
            "url": "https://example.org/app.git",
            "tag": "v1",
            "commit": "a" * 40,
        }
        original = source.copy()
        data = ExternalGitRepo.from_source("source.json", source)
        data.update()
        self.assertEqual(source, original)

    def test_commit_refresh_preserves_selected_ref(self):
        for ref in ({"tag": "v1"}, {"branch": "stable"}):
            with self.subTest(ref=ref):
                source = {
                    "type": "git",
                    "url": "https://example.org/app.git",
                    "commit": "a" * 40,
                    **ref,
                }
                data = ExternalGitRepo.from_source("source.json", source)
                expected = source | {"commit": "b" * 40}
                data.new_version = data.current_version._replace(commit="b" * 40)
                data.update()
                self.assertEqual(source, expected)
