import dataclasses
import io
import json
import unittest

from ruamel.yaml import YAML

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


class TestSourceFieldUpdates(unittest.TestCase):
    def test_new_yaml_commit_respects_merge_position(self):
        for fields in (
            "<<: {disable-submodules: true}\ntag: v1 # Tag comment\n",
            "tag: v1 # Tag comment\n<<: {disable-submodules: true}\n",
            "<<: {tag: v1, disable-submodules: true}\n",
        ):
            with self.subTest(fields=fields):
                original = (
                    "type: git\nurl: https://example.org/app.git\n"
                    + fields
                    + "# Destination\ndest: dependency\nx-checker-data:\n  type: git\n"
                )
                yaml = YAML()
                source = yaml.load(original)
                data = ExternalGitRepo.from_source("source.yaml", source)
                data.new_version = data.current_version._replace(commit="b" * 40)
                data.update()
                output = io.StringIO()
                yaml.dump(source, output)
                anchor = (
                    "tag: v1 # Tag comment\n"
                    if "tag: v1 # Tag comment" in fields
                    else "<<: {tag: v1, disable-submodules: true}\n"
                )
                expected = original.replace(
                    anchor, anchor + "commit: " + "b" * 40 + "\n"
                )
                if "tag: v1 # Tag comment" not in fields:
                    # Updating an inherited revision makes it explicit.
                    expected = expected.replace(anchor, anchor + "tag: v1\n")
                self.assertEqual(
                    output.getvalue(),
                    expected,
                )
                reloaded = yaml.load(output.getvalue())
                self.assertEqual(reloaded["commit"], "b" * 40)
                self.assertTrue(reloaded["disable-submodules"])

    def test_new_yaml_commit_follows_tag_and_preserves_comments(self):
        original = """# Source comment
type: git
url: https://example.org/app.git
tag: v1 # Tag comment
# Keep checkout in this directory
dest: dependency # Destination comment
x-checker-data: # Checker comment
  type: git
"""
        for inline_comment in (" # Tag comment", ""):
            with self.subTest(inline_comment=inline_comment):
                text = original.replace(" # Tag comment", inline_comment)
                yaml = YAML()
                source = yaml.load(text)
                data = ExternalGitRepo.from_source("source.yaml", source)
                data.new_version = data.current_version._replace(
                    tag="v2", commit="b" * 40
                )
                data.update()
                output = io.StringIO()
                yaml.dump(source, output)
                self.assertEqual(
                    output.getvalue(),
                    text.replace("tag: v1", "tag: v2").replace(
                        "# Keep checkout", "commit: " + "b" * 40 + "\n# Keep checkout"
                    ),
                )
                self.assertIs(data.source, source)

    def test_commit_order_for_json_and_yaml_sources(self):
        for loader in (json.loads, YAML().load):
            for ref in ("tag", "branch"):
                for commit_position in (None, 0, 3, 5):
                    with self.subTest(
                        loader=loader, ref=ref, commit_position=commit_position
                    ):
                        items = [
                            ("type", "git"),
                            ("url", "https://example.org/app.git"),
                            (ref, "v1" if ref == "tag" else "stable"),
                            ("dest", "dependency"),
                            ("x-checker-data", {"type": "git"}),
                        ]
                        if commit_position is not None:
                            items.insert(commit_position, ("commit", "a" * 40))
                        source = loader(json.dumps(dict(items)))
                        expected_keys = [key for key, _ in items]
                        if commit_position is None:
                            expected_keys.insert(3, "commit")
                        data = ExternalGitRepo.from_source("source.json", source)
                        data.new_version = data.current_version._replace(
                            commit="b" * 40
                        )
                        for _ in range(2):
                            data.update()
                            self.assertEqual(list(source), expected_keys)
                            self.assertEqual(source, dict(items) | {"commit": "b" * 40})
                            self.assertIs(data.source, source)

    def test_existing_yaml_commit_keeps_position_and_comments(self):
        original = """type: git
commit: old # Commit comment
url: https://example.org/app.git
tag: v1 # Tag comment
dest: dependency # Destination comment
# Checker configuration
x-checker-data:
  type: git
"""
        yaml = YAML()
        source = yaml.load(original)
        data = ExternalGitRepo.from_source("source.yaml", source)
        data.new_version = data.current_version._replace(tag="v2", commit="b" * 40)
        data.update()
        output = io.StringIO()
        yaml.dump(source, output)
        self.assertEqual(
            output.getvalue(),
            original.replace("commit: old", "commit: " + "b" * 40).replace(
                "tag: v1", "tag: v2"
            ),
        )

    def test_new_refs_stay_with_existing_commit(self):
        for loader in (json.loads, YAML().load):
            for ref in ("tag", "branch"):
                with self.subTest(loader=loader, ref=ref):
                    source = loader(
                        json.dumps(
                            {
                                "type": "git",
                                "url": "https://example.org/app.git",
                                "commit": "a" * 40,
                                "dest": "dependency",
                                "x-checker-data": {"type": "git"},
                            }
                        )
                    )
                    data = ExternalGitRepo.from_source("source.json", source)
                    data.new_version = data.current_version._replace(**{ref: "v2"})
                    data.update()
                    self.assertEqual(
                        list(source),
                        ["type", "url", ref, "commit", "dest", "x-checker-data"],
                    )
                    self.assertEqual(source[ref], "v2")
                    self.assertEqual(source["commit"], "a" * 40)

    def test_new_yaml_ref_preserves_neighboring_comments(self):
        original = """type: git
url: https://example.org/app.git # Repository

# Pinned revision
commit: old # Commit comment
x-checker-data:
  type: git
"""
        for ref in ("tag", "branch"):
            with self.subTest(ref=ref):
                yaml = YAML()
                source = yaml.load(original)
                data = ExternalGitRepo.from_source("source.yaml", source)
                data.new_version = data.current_version._replace(**{ref: "v2"})
                data.update()
                output = io.StringIO()
                yaml.dump(source, output)
                self.assertEqual(
                    output.getvalue(),
                    original.replace("# Repository\n", f"# Repository\n{ref}: v2\n"),
                )

    def test_new_extra_data_size_precedes_checker_data(self):
        for loader in (json.loads, YAML().load):
            with self.subTest(loader=loader):
                source = loader(
                    json.dumps(
                        {
                            "type": "extra-data",
                            "url": "https://example.org/app.tar.gz",
                            "sha256": "abc",
                            "filename": "app.tar.gz",
                            "x-checker-data": {"type": "json"},
                        }
                    )
                )
                data = ExternalData.from_source("source.json", source)
                data.new_version = data.current_version._replace(size=123)
                data.update()
                self.assertEqual(
                    list(source),
                    ["type", "url", "sha256", "size", "filename", "x-checker-data"],
                )
                self.assertEqual(source["size"], 123)


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
