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


class TestAppImageVersionChanges(unittest.TestCase):
    def test_version_changes(self):
        for source_type in ("file", "archive", "extra-data"):
            for (
                name,
                filename,
                changed_url,
                changed_checksum,
                current_version,
                new_version,
                expected,
            ) in (
                ("unchanged", "app.AppImage", False, False, None, "1", False),
                ("mirror", "app.AppImage", True, False, None, "1", False),
                ("stable URL", "app.AppImage", False, True, None, "2", True),
                ("new URL", "app.AppImage", True, True, None, "2", True),
                ("rebuild", "app.AppImage", False, True, "1", "1", False),
                ("known version", "app.AppImage", False, True, "1", "2", True),
                ("no version", "app.AppImage", True, True, None, None, False),
                ("ordinary stable URL", "app.tar.gz", False, True, None, "2", False),
                ("ordinary new URL", "app.tar.gz", True, True, None, "2", True),
            ):
                with self.subTest(source_type=source_type, case=name):
                    data = ExternalData.from_source(
                        "source.json",
                        {
                            "type": source_type,
                            "url": f"https://example.org/{filename}",
                            "sha256": "abc",
                            "size": 123,
                        },
                    )
                    self.assertFalse(data.has_version_changed)
                    data.current_version = data.current_version._replace(
                        version=current_version
                    )
                    data.new_version = data.current_version._replace(
                        url=f"https://example.org/new/{filename}"
                        if changed_url
                        else data.current_version.url,
                        checksum=MultiDigest(
                            md5="extra digest",
                            sha256="def" if changed_checksum else "abc",
                        ),
                        version=new_version,
                    )
                    self.assertEqual(data.has_version_changed, expected)

    def test_strongest_common_checksum_determines_content_change(self):
        data = ExternalData.from_source(
            "source.json",
            {
                "type": "file",
                "url": "https://example.org/app.AppImage",
                "sha256": "abc",
                "sha512": "strong",
            },
        )
        for sha256, sha512, expected in (
            ("different", "strong", False),
            ("abc", "different", True),
            ("abc", None, False),
        ):
            with self.subTest(sha256=sha256, sha512=sha512):
                data.new_version = data.current_version._replace(
                    checksum=MultiDigest(sha256=sha256, sha512=sha512), version="1"
                )
                self.assertEqual(data.has_version_changed, expected)

    def test_unchanged_content_with_changed_size_is_not_a_new_version(self):
        data = ExternalData.from_source(
            "source.json",
            {
                "type": "extra-data",
                "url": "https://example.org/app.AppImage",
                "sha256": "abc",
                "size": 123,
            },
        )
        data.set_new_version(data.current_version._replace(size=124, version="1.0"))
        self.assertIsNotNone(data.new_version)
        self.assertFalse(data.has_version_changed)


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
