import subprocess
import logging
import shutil
import tempfile
import unittest
import asyncio
from pathlib import Path

from src.checkers.submodulechecker import (
    SubmoduleChecker,
    Submodule,
    HashIndex,
    ModuleHash,
)


def _run_cmd(cmd):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        check=True,
    )


class TestSubmoduleChecker(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        logging.basicConfig(level=logging.INFO)

        cls.test_repo_dir = tempfile.TemporaryDirectory()
        cls.repo_path = Path(cls.test_repo_dir.name)

        subprocess.run(
            ["git", "config", "--global", "protocol.file.allow", "always"], check=False
        )

        _run_cmd(["git", "-C", str(cls.repo_path), "init", "--quiet"])
        _run_cmd(
            ["git", "-C", str(cls.repo_path), "config", "user.name", "Test Runner"]
        )
        _run_cmd(
            ["git", "-C", str(cls.repo_path), "config", "user.email", "test@localhost"]
        )

        (cls.repo_path / "test_module1.json").write_text(
            '{"name": "module1", "version": "1.0.0"}'
        )
        (cls.repo_path / "test_module2.json").write_text(
            '{"name": "module2", "version": "2.0.0"}'
        )

        _run_cmd(["git", "-C", str(cls.repo_path), "add", "."])
        _run_cmd(["git", "-C", str(cls.repo_path), "commit", "-m", "Initial commit"])

        cls.submodule_dir = tempfile.TemporaryDirectory()
        cls.submodule_path = Path(cls.submodule_dir.name)

        _run_cmd(["git", "-C", str(cls.submodule_path), "init", "--quiet"])
        _run_cmd(
            ["git", "-C", str(cls.submodule_path), "config", "user.name", "Test Runner"]
        )
        _run_cmd(
            [
                "git",
                "-C",
                str(cls.submodule_path),
                "config",
                "user.email",
                "test@localhost",
            ]
        )

        modules_dir = cls.submodule_path / "modules"
        modules_dir.mkdir()
        (modules_dir / "app1.yml").write_text("name: app1\nversion: 1.0.0")
        (modules_dir / "app2.yml").write_text("name: app2\nversion: 2.0.0")

        _run_cmd(["git", "-C", str(cls.submodule_path), "add", "."])
        _run_cmd(
            [
                "git",
                "-C",
                str(cls.submodule_path),
                "commit",
                "-m",
                "Initial submodule commit",
            ]
        )
        cls.initial_submodule_commit = (
            _run_cmd(["git", "-C", str(cls.submodule_path), "rev-parse", "HEAD"])
            .stdout.decode()
            .strip()
        )

        _run_cmd(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "-C",
                str(cls.repo_path),
                "submodule",
                "add",
                str(cls.submodule_path),
                "external-modules",
            ]
        )
        _run_cmd(["git", "-C", str(cls.repo_path), "add", "."])
        _run_cmd(["git", "-C", str(cls.repo_path), "commit", "-m", "Add submodule"])

        (modules_dir / "app1.yml").write_text("name: app1\nversion: 1.1.0")
        (modules_dir / "app3.yml").write_text("name: app3\nversion: 3.0.0")

        _run_cmd(["git", "-C", str(cls.submodule_path), "add", "."])
        _run_cmd(
            ["git", "-C", str(cls.submodule_path), "commit", "-m", "Update modules"]
        )
        cls.updated_submodule_commit = (
            _run_cmd(["git", "-C", str(cls.submodule_path), "rev-parse", "HEAD"])
            .stdout.decode()
            .strip()
        )

    @classmethod
    def tearDownClass(cls):
        cls.test_repo_dir.cleanup()
        cls.submodule_dir.cleanup()
        subprocess.run(
            ["git", "config", "--global", "--unset", "protocol.file.allow"], check=False
        )

    async def asyncSetUp(self):
        self.checker = SubmoduleChecker()
        self.test_dir = tempfile.TemporaryDirectory()
        self.test_path = Path(self.test_dir.name)

        await asyncio.to_thread(
            shutil.copytree, self.repo_path, self.test_path, dirs_exist_ok=True
        )

        self.manifest_path = self.test_path / "test.manifest.yml"
        self.manifest_path.write_text(
            "id: org.flatpak.Hello\n"
            "runtime: org.freedesktop.Platform\n"
            "runtime-version: '25.08'\n"
            "sdk: org.freedesktop.Sdk\n"
            "command: hello\n"
            "modules:\n"
            "  - external-modules/modules/app1.yml\n"
            "  - external-modules/modules/app2.yml\n"
            "  - name: hello\n"
            "    buildsystem: simple\n"
            "    build-commands:\n"
            "      - install -Dm755 hello.sh /app/bin/hello\n"
            "    sources:\n"
            "      - type: script\n"
            "        dest-filename: hello.sh\n"
            "        commands:\n"
            '          - echo "Hello world, from a sandbox"\n'
        )

    async def asyncTearDown(self):
        self.test_dir.cleanup()

    async def test_basic_submodule_discovery(self):
        modules_to_check = [
            "external-modules/modules/app1.yml",
            "external-modules/modules/app2.yml",
        ]

        submodules = await self.checker.check(
            modules_to_check, str(self.manifest_path), self.updated_submodule_commit
        )

        self.assertEqual(len(submodules), 1)
        submodule = submodules[0]
        self.assertEqual(submodule.path, "external-modules")
        self.assertGreaterEqual(len(submodule.modules), 1)
        self.assertIn("external-modules/modules/app1.yml", submodule.modules)

    async def test_module_hash_calculation(self):
        modules_to_check = ["external-modules/modules/app1.yml"]

        submodules = await self.checker.check(
            modules_to_check, str(self.manifest_path), self.updated_submodule_commit
        )

        self.assertEqual(len(submodules), 1)
        submodule = submodules[0]

        if "external-modules/modules/app1.yml" in submodule.modules:
            module_hash = submodule.modules["external-modules/modules/app1.yml"]
            self.assertTrue(module_hash.current)
            self.assertTrue(module_hash.updated)
            self.assertNotEqual(module_hash.current, module_hash.updated)
            self.assertTrue(module_hash.changed)

    async def test_no_changes_detected(self):
        modules_to_check = ["external-modules/modules/app1.yml"]

        _run_cmd(
            [
                "git",
                "-C",
                str(self.test_path / "external-modules"),
                "remote",
                "remove",
                "origin",
            ]
        )

        submodules = await self.checker.check(
            modules_to_check, str(self.manifest_path), ""
        )

        self.assertEqual(len(submodules), 1)

        outdated = self.checker.get_outdated_submodules()
        self.assertEqual(len(outdated), 0)

    async def test_module_not_in_submodule(self):
        modules_to_check = ["local-module.yml"]

        (self.test_path / "local-module.yml").write_text("name: local\nversion: 1.0.0")

        submodules = await self.checker.check(modules_to_check, str(self.manifest_path))

        for submodule in submodules:
            self.assertNotIn("local-module.yml", submodule.modules)

    async def test_multiple_modules_same_submodule(self):
        modules_to_check = [
            "external-modules/modules/app1.yml",
            "external-modules/modules/app2.yml",
            "external-modules/modules/app3.yml",
        ]

        submodules = await self.checker.check(
            modules_to_check, str(self.manifest_path), self.updated_submodule_commit
        )

        self.assertEqual(len(submodules), 1)
        submodule = submodules[0]

        self.assertGreaterEqual(len(submodule.modules), 1)
        self.assertIn("external-modules/modules/app1.yml", submodule.modules)

    async def test_submodule_update(self):
        modules_to_check = ["external-modules/modules/app1.yml"]

        await self.checker.check(
            modules_to_check, str(self.manifest_path), self.updated_submodule_commit
        )

        outdated = self.checker.get_outdated_submodules()
        self.assertEqual(len(outdated), 1)

        changes = await self.checker.update()

        self.assertEqual(len(changes), 1)
        self.assertIn("app1.yml", changes[0])
        self.assertIn("external-modules", changes[0])

        current_commit = (
            _run_cmd(
                [
                    "git",
                    "-C",
                    str(self.test_path / "external-modules"),
                    "rev-parse",
                    "HEAD",
                ]
            )
            .stdout.decode()
            .strip()
        )

        self.assertEqual(current_commit, self.updated_submodule_commit)

    async def test_no_updates_when_no_changes(self):
        modules_to_check = ["external-modules/modules/app1.yml"]

        _run_cmd(
            [
                "git",
                "-C",
                str(self.test_path / "external-modules"),
                "remote",
                "remove",
                "origin",
            ]
        )

        await self.checker.check(modules_to_check, str(self.manifest_path), "")

        outdated = self.checker.get_outdated_submodules()
        self.assertEqual(len(outdated), 0)

        changes = await self.checker.update()
        self.assertEqual(len(changes), 0)

    async def test_error_handling(self):
        modules_to_check = ["external-modules/modules/nonexistent.yml"]

        submodules = await self.checker.check(
            modules_to_check, str(self.manifest_path), self.updated_submodule_commit
        )

        errors = self.checker.get_errors()
        self.assertGreater(len(errors), 0)

        self.assertEqual(len(submodules), 1)

    async def test_relative_path_calculation(self):
        modules_to_check = ["external-modules/modules/app1.yml"]

        submodules = await self.checker.check(
            modules_to_check, str(self.manifest_path), self.updated_submodule_commit
        )

        self.assertEqual(len(submodules), 1)
        submodule = submodules[0]

        self.assertEqual(submodule.relative_path, "external-modules")

    async def test_cached_repo_optimization(self):
        modules_to_check = [
            "external-modules/modules/app1.yml",
            "external-modules/modules/app2.yml",
        ]

        await self.checker.check(
            modules_to_check, str(self.manifest_path), self.updated_submodule_commit
        )

        self.assertTrue(self.checker.cached_latest_repo)

    def test_submodule_class_basic_operations(self):
        submodule = Submodule("test/path", "relative/path")

        self.assertEqual(submodule.path, "test/path")
        self.assertEqual(submodule.relative_path, "relative/path")
        self.assertEqual(submodule.commit, "")

        submodule.add_module("test_module.yml")
        self.assertIn("test_module.yml", submodule.modules)

        submodule.set_module_hash("test_module.yml", "hash123", HashIndex.CURRENT)
        submodule.set_module_hash("test_module.yml", "hash456", HashIndex.UPDATED)

        current_hash = submodule.get_module_hash("test_module.yml", HashIndex.CURRENT)
        updated_hash = submodule.get_module_hash("test_module.yml", HashIndex.UPDATED)

        self.assertEqual(current_hash, "hash123")
        self.assertEqual(updated_hash, "hash456")

        self.assertTrue(submodule.has_changes)

    def test_module_hash_changed_detection(self):
        same_hash = ModuleHash(current="abc123", updated="abc123")
        self.assertFalse(same_hash.changed)

        diff_hash = ModuleHash(current="abc123", updated="def456")
        self.assertTrue(diff_hash.changed)

        no_current = ModuleHash(current="", updated="def456")
        self.assertFalse(no_current.changed)

        no_updated = ModuleHash(current="abc123", updated="")
        self.assertFalse(no_updated.changed)

    async def test_empty_modules_list(self):
        submodules = await self.checker.check([], str(self.manifest_path))

        self.assertEqual(len(submodules), 0)
        self.assertEqual(len(self.checker.get_errors()), 0)

    async def test_non_git_repository(self):
        non_git_dir = tempfile.TemporaryDirectory()
        non_git_manifest = Path(non_git_dir.name) / "test.manifest"
        non_git_manifest.write_text("test")

        modules_to_check = ["some_module.yml"]
        submodules = await self.checker.check(modules_to_check, str(non_git_manifest))

        self.assertEqual(len(submodules), 0)

        non_git_dir.cleanup()


if __name__ == "__main__":
    unittest.main()
