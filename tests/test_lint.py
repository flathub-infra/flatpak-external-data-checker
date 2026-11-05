import asyncio
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import main
from src.checkers.anityachecker import AnityaChecker
from src.lib.externaldata import ArchiveSource, ExternalGitRepo
from src.lint import ManifestLinter
from tests.support import TAG_PATTERN_CASES


class TestLint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value))
        return path

    def source(self, metadata):
        return {
            "type": "archive",
            "url": "https://example.invalid/source.tar.gz",
            "sha256": "0" * 64,
            "x-checker-data": metadata,
        }

    def lint(self, metadata):
        return ManifestLinter(1024 * 1024).lint(
            self.write("source.json", self.source(metadata))
        )

    def test_schema_errors_and_unknown_types(self):
        for metadata, expected in [
            ({"type": "unknown"}, "Unknown checker"),
            ({"type": "git"}, "does not support"),
            ({"type": "anitya"}, "project-id"),
            ({"type": "anitya", "project-id": "1"}, "not of type"),
            ({}, "required property"),
            ([], "not of type"),
        ]:
            with self.subTest(metadata=metadata):
                errors = self.lint(metadata)
                self.assertTrue(errors)
                self.assertIn(expected, "\n".join(errors))
                self.assertIn("source.json:$.x-checker-data", errors[0])

    def test_regex_validation(self):
        for pattern, expected in [
            ("[", "unterminated"),
            ("abc", "capture group"),
            ("", "capture group"),
        ]:
            with self.subTest(pattern=pattern):
                errors = self.lint(
                    {"type": "html", "url": "https://example.org", "pattern": pattern}
                )
                self.assertIn(expected, "\n".join(errors))

    def test_queries_compile_without_execution(self):
        metadata = {
            "type": "json",
            "tag-query": ".tag",
            "version-query": '$tag | sub("v"; "")',
            "url-query": 'error("must not execute") | $version',
        }
        self.assertEqual(self.lint(metadata), [])
        metadata["version-query"] = "$unknown"
        self.assertIn("not defined", "\n".join(self.lint(metadata)))
        metadata["version-query"] = ".["
        self.assertIn("syntax error", "\n".join(self.lint(metadata)))

    def test_github_selectors_and_policy_validation(self):
        base = {"type": "github", "repo": "pulsar-edit/pulsar"}
        for extra in (
            {},
            {"asset-name": "app.deb", "source-archive": "tarball"},
            {"source-archive": "tar.gz"},
            {"asset-name": "app.deb", "versions": {"<": "2"}},
            {"asset-name": "app.deb", "stable-only": False},
            {"asset-name": "app.deb", "repo": "https://github.com/o/r"},
            {"asset-name": "app.deb", "repo": "o/r/extra"},
            {"asset-name": "app.deb", "asset-pattern": ".*"},
        ):
            with self.subTest(extra=extra):
                self.assertTrue(self.lint(base | extra))
        for extra in (
            {
                "asset-name": "Linux.pulsar_${version}_amd64.deb",
                "tag-pattern": r"^v([\d.]+)$",
            },
            {"asset-name": "app-${tag}-${minor}-$$.tar.gz"},
            {"source-archive": "tarball"},
            {"source-archive": "zipball"},
        ):
            with self.subTest(extra=extra):
                self.assertEqual(self.lint(base | extra), [])

    def test_forgejo_metadata_and_templates(self):
        base = {
            "type": "forgejo",
            "instance": "https://codeberg.org",
            "repo": "example/app",
            "asset-name": "app-${tag}-${version}.tar.gz",
        }
        self.assertEqual(self.lint(base), [])
        self.assertEqual(self.lint(base | {"repo": "example.org/app"}), [])
        for extra in (
            {"asset-name": "$arch.tar.gz"},
            {"asset-name": "${tag"},
            {"tag-pattern": "(v)(.*)"},
            {"source-archive": "tarball"},
            {"instance": "https://codeberg.org:invalid"},
            {"repo": "example/.."},
        ):
            with self.subTest(extra=extra):
                self.assertTrue(self.lint(base | extra))

    def test_github_templates_and_patterns(self):
        base = {"type": "github", "repo": "o/r", "asset-name": "app.deb"}
        for extra, expected in (
            ({"tag-pattern": "["}, "unterminated"),
            ({"tag-pattern": "(v)(.*)"}, "capture group"),
            ({"asset-name": "$version_amd64.deb"}, "version_amd64"),
            ({"asset-name": "${tag"}, "Invalid template placeholder"),
            ({"asset-name": "$arch.deb"}, "Unknown template variable"),
        ):
            with self.subTest(extra=extra):
                self.assertIn(expected, "\n".join(self.lint(base | extra)))

    def test_tag_patterns(self):
        for checker in ("git", "github", "forgejo"):
            for pattern, _, _ in (*TAG_PATTERN_CASES, ("(v)(.*)", "", "")):
                with self.subTest(checker=checker, pattern=pattern):
                    metadata = {"type": checker, "tag-pattern": pattern}
                    if checker != "git":
                        metadata["repo"] = "o/r"
                    if checker == "forgejo":
                        metadata["instance"] = "https://codeberg.org"
                    path = self.write(
                        "git.json",
                        {
                            "type": "git",
                            "url": "https://codeberg.org/o/r.git",
                            "tag": "v1",
                            "x-checker-data": metadata,
                        },
                    )
                    errors = ManifestLinter(1024 * 1024).lint(path)
                    if pattern == "(v)(.*)":
                        self.assertIn("0 or 1 capture group", "\n".join(errors))
                    else:
                        self.assertEqual(errors, [])

    def test_html_pattern_group_error_explains_captures(self):
        for pattern in (".*", "(.*)", "(a)(b)(c)"):
            with self.subTest(pattern=pattern):
                errors = self.lint(
                    {
                        "type": "html",
                        "url": "https://example.org/downloads",
                        "pattern": pattern,
                    }
                )
                message = "\n".join(errors)
                self.assertIn("Group 1 must capture the download URL", message)
                self.assertIn("group 2 the version", message)
                self.assertIn("non-capturing groups (?:...)", message)

    def test_single_version_patterns(self):
        for metadata, field in (
            ({"type": "rotating-url", "url": "https://example.org/latest"}, "pattern"),
            (
                {
                    "type": "html",
                    "url": "https://example.org/releases",
                    "url-template": "https://example.org/app-$version.tar.gz",
                },
                "version-pattern",
            ),
        ):
            for pattern in (r"\d+(?:\.\d+)+", r"(\d+(?:\.\d+)+)", r"(\d+)(\.\d+)"):
                with self.subTest(field=field, pattern=pattern):
                    errors = self.lint(metadata | {field: pattern})
                    if pattern == r"(\d+)(\.\d+)":
                        self.assertIn("0 or 1 capture group", "\n".join(errors))
                        self.assertIn("non-capturing groups (?:...)", "\n".join(errors))
                    else:
                        self.assertEqual(errors, [])

    def test_github_git_has_no_artifact_selector(self):
        source = {
            "type": "git",
            "url": "https://github.com/o/r.git",
            "tag": "v1",
            "x-checker-data": {"type": "github", "repo": "o/r"},
        }
        path = self.write("git.json", source)
        self.assertEqual(ManifestLinter(1024 * 1024).lint(path), [])
        for extra in ({"asset-name": "app.tar.gz"}, {"source-archive": "tarball"}):
            source["x-checker-data"] = {"type": "github", "repo": "o/r", **extra}
            self.write("git.json", source)
            self.assertTrue(ManifestLinter(1024 * 1024).lint(path))

    def test_version_templates(self):
        for template, expected in [
            ("redeclipse_$version_nix.tar.gz", "'version_nix'"),
            ("boost_$major_$minor_$patch.tar.gz", "'major_'"),
            ("$versiondocs.7z", "'versiondocs'"),
            ("${version", "Invalid template placeholder"),
            ("$", "Invalid template placeholder"),
            ("$version01", "'version01'"),
            ("${version}_nix-$major.${minor}.$patch-$version0-$version12-$$", None),
        ]:
            for checker in ("anitya", "html"):
                with self.subTest(template=template, checker=checker):
                    metadata = {
                        "type": checker,
                        "url-template": "https://example.org/" + template,
                    }
                    if checker == "anitya":
                        metadata["project-id"] = 1
                    else:
                        metadata.update(
                            url="https://example.org", **{"version-pattern": "(.*)"}
                        )
                    errors = self.lint(metadata)
                    if expected:
                        self.assertIn(expected, "\n".join(errors))
                        self.assertIn(
                            "source.json:$.x-checker-data.url-template", errors[0]
                        )
                    else:
                        self.assertEqual(errors, [])

    def test_only_executed_version_templates_are_checked(self):
        source = {
            "type": "git",
            "url": "https://example.org/repo.git",
            "tag": "v1",
            "x-checker-data": {
                "type": "anitya",
                "project-id": 1,
                "tag-template": "v$major_$minor",
                "url-template": "$unused",
            },
        }
        path = self.write("git.json", source)
        errors = ManifestLinter(1024 * 1024).lint(path)
        self.assertEqual(len(errors), 1)
        self.assertIn("tag-template", errors[0])
        self.assertIn("'major_'", errors[0])
        source["x-checker-data"]["tag-template"] = "v${major}_${minor}"
        self.write("git.json", source)
        self.assertEqual(ManifestLinter(1024 * 1024).lint(path), [])
        self.assertEqual(
            self.lint(
                {
                    "type": "html",
                    "url": "https://example.org",
                    "pattern": "(url)(version)",
                    "url-template": "$unused",
                }
            ),
            [],
        )

    def test_git_queries_do_not_define_url(self):
        metadata = {
            "type": "json",
            "tag-query": '"v2"',
            "url-query": '"https://example.org"',
            "timestamp-query": "$url",
        }
        source = {
            "type": "git",
            "url": "https://example.org/repo.git",
            "tag": "v1",
            "x-checker-data": metadata,
        }
        path = self.write("git.json", source)
        errors = ManifestLinter(1024 * 1024).lint(path)
        self.assertIn("timestamp-query", "\n".join(errors))
        self.assertIn("$url is not defined", "\n".join(errors))

        # Git never executes url-query or an orphaned data-url expression.
        metadata["url-query"] = ".["
        metadata["version-data-url"] = ".["
        metadata["timestamp-query"] = '$tag | "2026-01-01T00:00:00Z"'
        self.write("git.json", source)
        self.assertEqual(ManifestLinter(1024 * 1024).lint(path), [])

    def test_data_url_uses_only_preceding_variables(self):
        metadata = {
            "type": "json",
            "version-query": '"1"',
            "url-data-url": '$version | "https://example.org"',
            "url-query": ".url",
        }
        self.assertEqual(self.lint(metadata), [])
        metadata["url-data-url"] = "$url"
        self.assertIn("$url is not defined", "\n".join(self.lint(metadata)))

    def test_malformed_unannotated_source_exits_cleanly(self):
        path = self.write(
            "manifest.json",
            {
                "name": "test",
                "sources": [{"type": "archive", "url": 123, "sha256": "0" * 64}],
            },
        )
        result = subprocess.run(
            [sys.executable, "flatpak-external-data-checker", "--lint", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(str(path), result.stderr)
        self.assertIn("Constructor parameter should be str", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_loader_errors_fail_lint(self):
        path = self.write(
            "source.json",
            {"type": "archive", "url": "https://example.org/source.tar.gz"},
        )
        result = subprocess.run(
            [sys.executable, "flatpak-external-data-checker", "--lint", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{path}:$: Data is missing checksum", result.stderr)
        self.assertIn("Lint finished with 1 error(s)", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_offline_cli_and_referenced_yaml(self):
        module = self.root / "module.yml"
        module.write_text("name: test\nsources:\n  - source.json\n")
        source = self.write(
            "source.json",
            self.source(
                {
                    "type": "anitya",
                    "project-id": 1,
                    "url-template": "https://example.org/$version",
                }
            ),
        )
        root = self.write(
            "app.json", {"app-id": "org.example.App", "modules": ["module.yml"]}
        )
        original = {p: p.read_bytes() for p in (root, module, source)}
        args = main.parse_cli_args(["--lint", str(root)])
        with (
            patch(
                "aiohttp.ClientSession", side_effect=AssertionError("network session")
            ),
            patch(
                "src.main.manifest.ManifestChecker.check",
                side_effect=AssertionError("online check"),
            ),
        ):
            self.assertEqual(asyncio.run(main.run_with_args(args)), (0, 0, False))
        for path, contents in original.items():
            self.assertEqual(path.read_bytes(), contents)

    def test_missing_reference_and_cycle(self):
        root = self.write(
            "app.json",
            {"app-id": "org.example.App", "modules": ["missing.json", "app.json"]},
        )
        errors = ManifestLinter(1024).lint(root)
        self.assertEqual(len(errors), 2)
        self.assertIn("missing.json", errors[0])
        self.assertIn("Circular", errors[1])

    def test_symlinked_manifests_keep_relative_includes(self):
        (self.root / "shared").mkdir()
        (self.root / "app").mkdir()
        self.write("shared/module.json", {"name": "dep", "sources": ["source.json"]})
        module = self.root / "app/module.json"
        module.symlink_to("../shared/module.json")
        self.write(
            "app/source.json",
            self.source(
                {
                    "type": "anitya",
                    "project-id": 1,
                    "url-template": "https://example.org/$version",
                }
            ),
        )
        app = self.write(
            "app/app.json", {"app-id": "org.example.App", "modules": ["module.json"]}
        )
        for path in (module, app):
            with self.subTest(path=path):
                self.assertEqual(ManifestLinter(1024 * 1024).lint(path), [])

    def test_symlink_name_selects_manifest_parser(self):
        target = self.root / "source.txt"
        target.write_text(
            "type: git\nurl: https://example.org/repo.git\n"
            "x-checker-data:\n  type: git\n"
        )
        path = self.root / "source.yaml"
        path.symlink_to(target.name)
        self.assertEqual(ManifestLinter(1024 * 1024).lint(path), [])

    def test_symlink_cycles(self):
        path = self.write("module.json", {"name": "dep", "modules": ["alias.json"]})
        (self.root / "alias.json").symlink_to(path.name)
        errors = ManifestLinter(1024 * 1024).lint(path)
        self.assertEqual(len(errors), 1)
        self.assertIn("alias.json:$: Circular manifest reference", errors[0])
        loop = self.root / "loop.json"
        loop.symlink_to(loop.name)
        self.assertTrue(ManifestLinter(1024 * 1024).lint(loop))

    def test_invalid_manifests_exit_cleanly(self):
        for name, text in [
            ("bad.json", "{"),
            ("bad.yml", "modules: ["),
            ("empty.yml", ""),
            ("empty.json", "   \n"),
            ("scalar.json", "42"),
        ]:
            with self.subTest(name=name):
                path = self.root / name
                path.write_text(text)
                result = subprocess.run(
                    [
                        sys.executable,
                        "flatpak-external-data-checker",
                        "--lint",
                        str(path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(str(path), result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_size_limit_and_multiple_errors(self):
        path = self.write(
            "sources.json",
            [self.source({"type": "unknown"}), self.source({"type": "other"})],
        )
        self.assertEqual(len(ManifestLinter(1024 * 1024).lint(path)), 2)
        self.assertIn("maximum file size", ManifestLinter(1).lint(path)[0])

    def test_update_options_rejected(self):
        for option in ("--update", "--edit-only", "--commit-only", "--check-outdated"):
            with (
                self.subTest(option=option),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as err,
            ):
                main.parse_cli_args(["--lint", option, "manifest.json"])
            self.assertEqual(err.exception.code, 2)

    def test_nested_external_sources(self):
        root = self.write("app.json", {"name": "test", "sources": ["sources.json"]})
        self.write("sources.json", ["nested.json"])
        self.write("nested.json", self.source({"type": "unknown"}))
        self.assertIn("Nested external", ManifestLinter(1024).lint(root)[0])

    def test_recursive_yaml_alias(self):
        root = self.root / "module.yml"
        root.write_text("&module\nname: recursive\nmodules: [*module]\n")
        self.assertIn("Circular YAML alias", ManifestLinter(1024).lint(root)[0])

    def test_schema_lookup_for_source_types(self):
        self.assertIn(
            "url-template", AnityaChecker.get_json_schema(ArchiveSource)["required"]
        )
        self.assertIn(
            "tag-template", AnityaChecker.get_json_schema(ExternalGitRepo)["required"]
        )

    def test_named_source_is_not_a_module(self):
        source = self.source({"type": "unknown"})
        source.update(type="extra-data", name="vendor", filename="vendor.bin", size=1)
        path = self.write("source.json", source)
        self.assertIn("Unknown checker", "\n".join(ManifestLinter(1024).lint(path)))

    def test_parent_relationships(self):
        def source(ident, parent=None):
            metadata = {"type": "git", "source-id": ident}
            if parent is not None:
                metadata["parent-id"] = parent
            return {
                "type": "git",
                "url": "https://example.org/repo.git",
                "tag": "v1",
                "x-checker-data": metadata,
            }

        for sources, expected in [
            ([source("child", "missing")], "not found"),
            ([source("self", "self")], "inheritance loop"),
            ([source("a", "b"), source("b", "a")], "inheritance loop"),
            ([source("child", "parent"), source("parent")], None),
        ]:
            with self.subTest(sources=sources):
                path = self.write("sources.json", sources)
                errors = ManifestLinter(1024 * 1024).lint(path)
                if expected:
                    self.assertIn(expected, "\n".join(errors))
                    self.assertIn(str(path), errors[0])
                else:
                    self.assertEqual(errors, [])

    def test_implicit_parent_in_referenced_module(self):
        self.write(
            "parent.json",
            {
                "name": "parent",
                "sources": [
                    {
                        "type": "git",
                        "url": "https://example.org/parent.git",
                        "tag": "v1",
                    }
                ],
            },
        )
        child = {
            "type": "git",
            "url": "https://example.org/child.git",
            "tag": "v1",
            "x-checker-data": {"type": "git", "parent-id": "parent-git-0"},
        }
        self.write("child.json", child)
        root = self.write(
            "app.json",
            {
                "app-id": "org.example.App",
                "modules": [
                    "parent.json",
                    {"name": "child", "sources": ["child.json"]},
                ],
            },
        )
        self.assertEqual(ManifestLinter(1024 * 1024).lint(root), [])
