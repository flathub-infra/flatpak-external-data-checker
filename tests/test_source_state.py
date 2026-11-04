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
