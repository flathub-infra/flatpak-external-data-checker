import hashlib
import tempfile
import logging
import subprocess
import shutil
import asyncio
import typing as t
from pathlib import Path
from enum import Enum
from dataclasses import dataclass

log = logging.getLogger(__name__)


class HashIndex(Enum):
    CURRENT = 0
    UPDATED = 1


@dataclass
class ModuleHash:
    current: str = ""
    updated: str = ""

    @property
    def changed(self) -> bool:
        return bool(self.current and self.updated and self.current != self.updated)


class Submodule:
    def __init__(self, path: str, relative_path: str):
        self._path: str = path
        self._commit: str = ""
        self._modules: t.Dict[str, ModuleHash] = {}
        self._relative_path: str = relative_path

    @property
    def path(self) -> str:
        return self._path

    @property
    def relative_path(self) -> str:
        return self._relative_path

    @property
    def commit(self) -> str:
        return self._commit

    @commit.setter
    def commit(self, new_commit: str) -> None:
        self._commit = new_commit

    @property
    def modules(self) -> t.Dict[str, ModuleHash]:
        return self._modules

    def add_module(self, module: str) -> None:
        if module not in self._modules:
            self._modules[module] = ModuleHash()

    def set_module_hash(
        self, module: str, hash_value: str, hash_type: HashIndex
    ) -> None:
        if module in self._modules:
            if hash_type == HashIndex.CURRENT:
                self._modules[module].current = hash_value
            else:
                self._modules[module].updated = hash_value

    def get_module_hash(self, module: str, hash_type: HashIndex) -> str:
        module_hash = self._modules.get(module)
        if module_hash:
            return (
                module_hash.current
                if hash_type == HashIndex.CURRENT
                else module_hash.updated
            )
        return ""

    @property
    def has_changes(self) -> bool:
        return any(module_hash.changed for module_hash in self._modules.values())


class SubmoduleChecker:
    def __init__(self) -> None:
        self.submodules: t.List[Submodule] = []
        self._errors: t.List[Exception] = []
        self.working_manifest_dir: t.Optional[Path] = None
        self.working_git_top_level_dir: t.Optional[Path] = None
        self.cached_latest_repo: bool = False

    def _run_cmd(self, cmd: t.List[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, stdout=subprocess.PIPE, check=True)

    async def _prepare_submodules(self) -> None:
        await asyncio.to_thread(
            self._run_cmd,
            [
                "git",
                "-C",
                str(self.current_git_top_level_dir),
                "submodule",
                "update",
                "--quiet",
                "--init",
            ],
        )

        top_level_submodule_paths = await asyncio.to_thread(
            self._run_cmd,
            [
                "git",
                "-C",
                str(self.current_git_top_level_dir),
                "submodule",
                "foreach",
                "--quiet",
                "echo $displaypath",
            ],
        )

        top_level_paths_split = top_level_submodule_paths.stdout.decode(
            "utf-8"
        ).splitlines()

        if not top_level_paths_split:
            return

        for submodule_path in top_level_paths_split:
            absolute_submodule_dir = self.working_git_top_level_dir / submodule_path
            relative_submodule_path = absolute_submodule_dir.relative_to(
                self.working_manifest_dir
            )

            submodule = Submodule(
                submodule_path,
                str(relative_submodule_path),
            )
            self.submodules.append(submodule)

    async def check(
        self,
        relative_modules_paths: t.List[str],
        manifest_path: str,
        test_debug_hardcode_update: str = "",
    ) -> t.List[Submodule]:
        self.submodules.clear()
        self._errors.clear()
        self.working_manifest_dir = Path(manifest_path).parent

        if not relative_modules_paths:
            log.info("No external module files referenced. Skipping submodule checks")
            return self.submodules

        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.working_manifest_dir),
                    "submodule",
                    "status",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except subprocess.CalledProcessError:
            log.info(
                "Cannot check git submodules as this is not a valid git repository"
            )
            return self.submodules

        self.working_git_top_level_dir = Path(
            self._run_cmd(
                [
                    "git",
                    "-C",
                    str(self.working_manifest_dir),
                    "rev-parse",
                    "--show-toplevel",
                ]
            )
            .stdout.decode("utf-8")
            .strip()
        )

        git_dir = Path(
            self._run_cmd(
                [
                    "git",
                    "-C",
                    str(self.working_git_top_level_dir),
                    "rev-parse",
                    "--git-dir",
                ]
            )
            .stdout.decode("utf-8")
            .strip()
        )

        with tempfile.TemporaryDirectory() as checking_dir:
            self.current_git_top_level_dir = Path(checking_dir) / "current"
            self.updated_git_top_level_dir = Path(checking_dir) / "updated"

            self.current_git_top_level_dir.mkdir()
            self.updated_git_top_level_dir.mkdir()

            await asyncio.to_thread(
                shutil.copytree,
                self.working_git_top_level_dir / git_dir,
                self.current_git_top_level_dir / git_dir,
            )

            await self._prepare_submodules()

            for module_path in relative_modules_paths:
                submodule = self._module_in_submodule(module_path)
                if submodule.path:
                    log.info("Checking %s in submodule %s", module_path, submodule.path)
                    await self._check_module_hash(module_path, submodule)
                else:
                    log.debug("Skipping %s", module_path)

        return self.submodules

    def get_errors(self) -> t.List[Exception]:
        return self._errors.copy()

    async def update(self) -> t.List[str]:
        submodule_changes: t.List[str] = []

        for submodule in self.submodules:
            if submodule.commit and submodule.modules:
                await self._update_submodule_commit(submodule)
                for flatpak_module in submodule.modules:
                    if submodule.get_module_hash(flatpak_module, HashIndex.UPDATED):
                        change_text = (
                            f"Update {flatpak_module} "
                            f"in submodule {submodule.relative_path}"
                        )
                        submodule_changes.append(change_text)

        return submodule_changes

    def _module_in_submodule(self, module_path: str) -> Submodule:
        module_abs_path = (self.working_manifest_dir / module_path).resolve()

        for submodule in self.submodules:
            submodule_abs_path = (
                self.working_git_top_level_dir / submodule.path
            ).resolve()
            try:
                if submodule_abs_path in [module_abs_path, *module_abs_path.parents]:
                    return submodule
            except ValueError:
                continue

        return Submodule("", "")

    async def _check_module_hash(self, module_path: str, submodule: Submodule) -> None:
        from_git_top_level_module_path = (
            self.working_manifest_dir / module_path
        ).relative_to(self.working_git_top_level_dir)

        current_module_path = (
            self.current_git_top_level_dir / from_git_top_level_module_path
        )
        current_hash = await self._get_module_hash(
            current_module_path, module_path, submodule, False
        )

        await self._get_latest_submodule(submodule)

        updated_module_path = (
            self.updated_git_top_level_dir / from_git_top_level_module_path
        )
        updated_hash = await self._get_module_hash(
            updated_module_path, module_path, submodule, True
        )

        if current_hash and updated_hash and current_hash != updated_hash:
            submodule.add_module(module_path)
            submodule.set_module_hash(module_path, current_hash, HashIndex.CURRENT)
            submodule.set_module_hash(module_path, updated_hash, HashIndex.UPDATED)

    async def _get_module_hash(
        self,
        module_path: Path,
        relative_module_path: str,
        submodule: Submodule,
        updated_path: bool,
    ) -> str:
        try:
            with open(module_path, "rb") as f:
                return hashlib.sha256(f.read()).hexdigest()
        except IOError as err:
            log.error("Failed to open module %s: %s", relative_module_path, err)
            self._errors.append(err)
            return ""

    async def _get_latest_submodule(self, submodule: Submodule) -> str:
        if not submodule.commit:
            if not self.cached_latest_repo:
                await asyncio.to_thread(
                    shutil.copytree,
                    self.current_git_top_level_dir / ".git",
                    self.updated_git_top_level_dir / ".git",
                )
                self.cached_latest_repo = True

            await asyncio.to_thread(self._update_submodule, submodule)

            new_commit = self._run_cmd(
                [
                    "git",
                    "-C",
                    str(self.updated_git_top_level_dir / submodule.path),
                    "rev-parse",
                    "HEAD",
                ]
            )
            submodule.commit = new_commit.stdout.decode("utf-8").strip()

        return str(self.updated_git_top_level_dir)

    def _update_submodule(self, submodule: Submodule) -> None:
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.updated_git_top_level_dir),
                    "submodule",
                    "update",
                    "--init",
                    "--remote",
                    submodule.path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )
        except subprocess.CalledProcessError as err:
            log.error("Failed to update submodule %s: %s", submodule.relative_path, err)
            self._errors.append(err)

    async def _update_submodule_commit(self, submodule: Submodule) -> None:
        await asyncio.to_thread(
            self._run_cmd,
            [
                "git",
                "-C",
                str(self.working_git_top_level_dir),
                "submodule",
                "update",
                "--init",
                "--remote",
                submodule.path,
            ],
        )

        self._run_cmd(
            [
                "git",
                "-C",
                str(self.working_git_top_level_dir / submodule.path),
                "checkout",
                submodule.commit,
            ]
        )

    def get_outdated_submodules(self) -> t.List[Submodule]:
        return [s for s in self.submodules if s.has_changes]
