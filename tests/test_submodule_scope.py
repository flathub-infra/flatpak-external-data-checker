import itertools
import json
import subprocess
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from src.lib.errors import ManifestScopeError
from src.lib.submodules import submodule_reference_path
from src.manifest import ManifestChecker


class TestSubmoduleScope(unittest.TestCase):
    @staticmethod
    @contextmanager
    def app_repository() -> Iterator[Path]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "app"
            subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
            yield root

    @staticmethod
    def track_submodule(root: Path, path: str, *, configured: bool = True) -> None:
        subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "update-index",
                "--add",
                "--cacheinfo",
                "160000",
                "a" * 40,
                path,
            ],
            check=True,
        )
        if configured:
            with (root / ".gitmodules").open("a") as config:
                config.write(
                    f'[submodule "{path}"]\n'
                    f"\tpath = {path}\n"
                    "\turl = https://example.com/submodule.git\n"
                )

    @staticmethod
    def write_app_manifest(
        root: Path, kind: str, include: str, local_source: str | None = None
    ) -> Path:
        modules = []
        sources = []
        if kind == "module":
            modules.append(include)
        else:
            sources.append(include)
        if local_source is not None:
            sources.append(local_source)
        if sources:
            modules.append({"name": "local", "sources": sources})
        path = root / "app.json"
        path.write_text(json.dumps({"id": "org.test.App", "modules": modules}))
        return path

    def test_includes_do_not_load_submodules(self):
        for initialized, submodule in itertools.product(
            (False, True),
            (
                "flathub-shared-modules",
                "shared-modules",
                "shared modules",
                "vendor/shared-modules",
            ),
        ):
            for kind in ("module", "sources"):
                for symlink in (False, True):
                    with (
                        self.subTest(
                            initialized=initialized,
                            kind=kind,
                            symlink=symlink,
                            submodule=submodule,
                        ),
                        self.app_repository() as root,
                    ):
                        self.track_submodule(root, submodule)
                        shared = root / submodule
                        shared.mkdir(parents=True)
                        if initialized:
                            subprocess.run(
                                ["git", "init", "-q", str(shared)], check=True
                            )
                            # Loading this file would fail even before checking sources.
                            (shared / "include.json").write_text("not valid JSON")
                        if symlink:
                            (root / "alias").symlink_to(
                                shared, target_is_directory=True
                            )
                        include = (
                            "alias/include.json"
                            if symlink
                            else f"{submodule}/include.json"
                        )
                        source = {
                            "type": "file",
                            "url": "https://example.com/local",
                            "sha256": "b" * 64,
                        }
                        (root / "local.json").write_text(json.dumps(source))
                        path = self.write_app_manifest(
                            root, kind, include, local_source="local.json"
                        )
                        with self.assertLogs("src.manifest", level="INFO") as logs:
                            checker = ManifestChecker(str(path))
                        self.assertTrue(any("Loading" in line for line in logs.output))
                        self.assertTrue(
                            any("tracked submodule" in line for line in logs.output)
                        )
                        self.assertFalse(
                            any(
                                "coverage is incomplete" in line for line in logs.output
                            )
                        )
                        self.assertEqual(len(checker.get_external_data()), 1)
                        self.assertEqual(checker.get_errors(), [])
                        self.assertIn(
                            root / include, checker.submodule_checker.references
                        )
                        self.assertEqual(checker.get_report_errors(), [])
                        self.assertEqual(len(checker._incomplete_manifests), 1)
                        report = checker.get_source_report()
                        self.assertEqual(len(report), 1)
                        self.assertEqual(report[0]["status"], "unchecked")
                        self.assertEqual(report[0]["manifest"], "local.json")

    def test_external_includes_remain_scope_errors(self):
        with self.app_repository() as root:
            outside = root.parent / "outside"
            (outside / ".git").mkdir(parents=True)
            (outside / "include.json").write_text("not valid JSON")
            self.track_submodule(root, "redirected")
            (root / "redirected").symlink_to(outside, target_is_directory=True)
            self.assert_scope_error(root, "../outside/include.json")
            self.assert_scope_error(root, "redirected/include.json")

            shared = root / "flathub-shared-modules"
            shared.mkdir()
            subprocess.run(["git", "init", "-q", str(shared)], check=True)
            (shared / "include.json").write_text("not valid JSON")
            self.assert_scope_error(root, f"{shared.name}/include.json")
            self.track_submodule(root, shared.name, configured=False)
            self.assert_scope_error(root, f"{shared.name}/include.json")
            self.track_submodule(root, shared.name)

            nested = shared / "nested"
            (nested / ".git").mkdir(parents=True)
            (nested / "include.json").write_text("not valid JSON")
            self.assert_scope_error(root, f"{shared.name}/nested/include.json")
            self.assert_scope_error(
                root, f"{shared.name}/nested/../../{shared.name}/include.json"
            )
            untracked = root / "untracked"
            (untracked / ".git").mkdir(parents=True)
            (untracked / "alias.json").symlink_to(shared / "include.json")
            self.assert_scope_error(root, "untracked/alias.json")

            (shared / "external.json").symlink_to(outside / "include.json")
            self.assert_scope_error(root, f"{shared.name}/external.json")
            (root / "local.json").write_text("not valid JSON")
            (shared / "app.json").symlink_to(root / "local.json")
            self.assert_scope_error(root, f"{shared.name}/app.json")
            (root / "alias").symlink_to(shared, target_is_directory=True)
            self.assert_scope_error(root, "alias/app.json")
            (root / "file-alias.json").symlink_to(shared / "app.json")
            self.assert_scope_error(root, "file-alias.json")
            (root / "back.json").symlink_to(shared / "include.json")
            (shared / "escape-and-return.json").symlink_to(root / "back.json")
            self.assert_scope_error(root, f"{shared.name}/escape-and-return.json")

            (root / ".gitmodules").write_text("[invalid\n")
            self.assert_scope_error(root, f"{shared.name}/include.json")

    def assert_scope_error(self, root: Path, include: str) -> None:
        for kind in ("module", "sources"):
            with self.subTest(kind=kind, include=include):
                path = self.write_app_manifest(root, kind, include)
                with self.assertLogs("src.manifest", level="WARNING"):
                    checker = ManifestChecker(str(path))
                errors = checker.get_report_errors()
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], ManifestScopeError)
                self.assertEqual(checker.get_external_data(), [])

    def test_scope_starts_at_app_and_checks_intermediate_aliases(self):
        for kind, location in itertools.product(
            ("module", "sources"), ("enclosing", "app", "submodule", "parent-step")
        ):
            with (
                self.subTest(kind=kind, location=location),
                self.app_repository() as root,
            ):
                shared = root / "flathub-shared-modules"
                self.track_submodule(root, shared.name)
                shared.mkdir()
                target = shared / "include.json"
                target.write_text("not valid JSON")
                if location == "enclosing":
                    subprocess.run(["git", "init", "-q", str(root.parent)], check=True)
                    path = self.write_app_manifest(
                        root, kind, str(target.relative_to(root))
                    )
                    self.assertEqual(ManifestChecker(str(path)).get_report_errors(), [])
                    with tempfile.TemporaryDirectory() as directory:
                        alias = Path(directory) / "outer"
                        alias.symlink_to(root.parent, target_is_directory=True)
                        via_alias = alias / root.name / path.name
                        self.assertEqual(
                            ManifestChecker(str(via_alias)).get_report_errors(), []
                        )
                    continue

                alias_dir = shared if location == "submodule" else root
                nested = alias_dir / "nested"
                subprocess.run(["git", "init", "-q", str(nested)], check=True)
                (nested / "link.json").symlink_to(target)
                alias = alias_dir / "alias.json"
                if location == "parent-step":
                    alias.symlink_to(nested / ".." / shared.name / target.name)
                else:
                    alias.symlink_to(nested / "link.json")
                path = self.write_app_manifest(root, kind, str(alias.relative_to(root)))
                errors = ManifestChecker(str(path)).get_report_errors()
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], ManifestScopeError)

    def test_parent_paths_preserve_include_scope(self):
        for location in ("app", "alias", "reentry", "internal-alias", "alias-exit"):
            with (
                self.subTest(location=location),
                self.app_repository() as root,
            ):
                shared = root / "flathub-shared-modules"
                self.track_submodule(root, shared.name)
                nested = shared / "nested"
                nested.mkdir(parents=True)
                (root / "include.json").write_text(
                    json.dumps({"name": "local", "sources": []})
                )
                if location == "alias":
                    (root / "alias").symlink_to(nested, target_is_directory=True)
                    (shared / "include.json").write_text("not valid JSON")
                    include = "alias/../include.json"
                    expected = nested / "../include.json"
                elif location == "reentry":
                    (root / "alias").symlink_to(shared, target_is_directory=True)
                    (shared / "include.json").write_text("not valid JSON")
                    include = f"{shared.name}/../alias/include.json"
                    expected = shared / "include.json"
                elif location == "internal-alias":
                    (nested / "child").mkdir()
                    (shared / "alias").symlink_to(
                        "nested/child", target_is_directory=True
                    )
                    (nested / "include.json").write_text("not valid JSON")
                    include = f"{shared.name}/alias/../include.json"
                    expected = root / include
                elif location == "alias-exit":
                    (shared / "alias").symlink_to("nested", target_is_directory=True)
                    include = f"{shared.name}/alias/../../include.json"
                    expected = None
                else:
                    include = f"{shared.name}/../include.json"
                    expected = None
                path = self.write_app_manifest(root, "module", include)
                checker = ManifestChecker(str(path))
                self.assertEqual(checker.get_report_errors(), [])
                self.assertEqual(
                    checker.submodule_checker.references, {root / include: True}
                )
                self.assertEqual(
                    submodule_reference_path(root / include, shared), expected
                )
                self.assertEqual(
                    len(checker._incomplete_manifests), int(expected is not None)
                )

    def test_unrelated_redirected_gitlink_does_not_affect_tracked_include(self):
        with self.app_repository() as root:
            self.track_submodule(root, "shared-modules")
            self.track_submodule(root, "redirected")
            shared = root / "shared-modules"
            shared.mkdir()
            (shared / "include.json").write_text("not valid JSON")
            (root / "redirected").symlink_to(shared, target_is_directory=True)
            path = self.write_app_manifest(
                root, "module", "shared-modules/include.json"
            )
            checker = ManifestChecker(str(path))
            self.assertEqual(checker.get_report_errors(), [])

    def test_parent_step_between_initialized_gitlinks(self):
        with self.app_repository() as root:
            for name in ("first", "shared-modules"):
                self.track_submodule(root, name)
                directory = root / name
                directory.mkdir()
                subprocess.run(["git", "init", "-q", str(directory)], check=True)
            (root / "shared-modules/include.json").write_text("not valid JSON")
            path = self.write_app_manifest(
                root, "module", "first/../shared-modules/include.json"
            )
            self.assertEqual(ManifestChecker(str(path)).get_report_errors(), [])

    def test_submodule_path_preserves_carriage_return(self):
        with self.app_repository() as root:
            name = "shared\rmodules"
            self.track_submodule(root, name, configured=False)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "config",
                    "--file",
                    ".gitmodules",
                    "submodule.shared.path",
                    name,
                ],
                check=True,
            )
            path = self.write_app_manifest(root, "module", f"{name}/include.json")
            self.assertEqual(ManifestChecker(str(path)).get_report_errors(), [])
