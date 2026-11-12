import dataclasses
import logging
import os
import posixpath
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from gi.repository import GLib
from ruamel.yaml.error import YAMLError

from .errors import CheckerError
from .releaselinks import comparison_url
from .sourceupdates import SourceUpdate
from .utils import read_manifest

log = logging.getLogger(__name__)


def local_source_paths(source: Mapping[str, object]) -> list[str]:
    paths = source.get("paths", [])
    return [
        path
        for path in [source.get("path"), *(paths if isinstance(paths, list) else [])]
        if isinstance(path, str)
    ]


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "submodule.recurse=false", "-C", str(directory), *args],
        capture_output=True,
        text=True,
        errors="surrogateescape",
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise CheckerError(f"Git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout


@dataclasses.dataclass(frozen=True)
class SubmoduleUpdate:
    path: str
    url: str
    current: str
    candidate: str
    files: tuple[str, ...]

    def source_update(self) -> SourceUpdate:
        return SourceUpdate(
            f"Submodule {self.path}",
            self.current[:12],
            self.candidate[:12],
            None,
            comparison_url(self.url, self.current, self.candidate),
        )


class SubmoduleChecker:
    def __init__(self, repository: Path | None, max_manifest_size: int) -> None:
        self.repository = repository
        self.max_manifest_size = max_manifest_size
        self.references: dict[Path, bool] = {}
        self.updates: list[SubmoduleUpdate] = []
        self.errors: list[Exception] = []

    def add_reference(self, path: str, *, manifest: bool) -> None:
        absolute = Path(os.path.abspath(path))
        self.references[absolute] = self.references.get(absolute, False) or manifest

    def reset(self) -> None:
        self.updates = []
        self.errors = []

    @staticmethod
    def _relative_reference(reference: Path, base: Path) -> Path | None:
        if reference.is_relative_to(base):
            return reference.relative_to(base)
        # Resolve aliases into the submodule, but not symlinks within it.
        for ancestor in [*reversed(reference.parents), reference]:
            resolved = ancestor.resolve()
            if resolved.is_relative_to(base):
                return resolved.relative_to(base) / reference.relative_to(ancestor)
        return None

    def _config(self, name: str, key: str) -> str:
        assert self.repository is not None
        for options in ((), ("--file", ".gitmodules")):
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self.repository),
                    "config",
                    *options,
                    "--get",
                    f"submodule.{name}.{key}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        return ""

    def _url(self, name: str) -> str:
        url = self._config(name, "url")
        if not url:
            raise CheckerError(f"Submodule {name} has no URL")
        if url.startswith(("./", "../")):
            assert self.repository is not None
            origin = git(self.repository, "remote", "get-url", "origin").strip()
            # Git resolves relative URLs against the repository URL as a directory.
            if "://" in origin:
                prefix, path = origin.split("://", 1)
                host, _, path = path.partition("/")
                url = f"{prefix}://{host}/{posixpath.normpath(path + '/' + url)}"
            elif ":" in origin:
                host, path = origin.split(":", 1)
                url = host + ":" + posixpath.normpath(path + "/" + url)
            else:
                url = os.path.normpath(os.path.join(origin, url))
        return url

    def _snapshot(
        self, checkout: Path, references: Mapping[Path, bool]
    ) -> dict[str, str]:
        entries = {
            ".": "040000 tree " + git(checkout, "rev-parse", "HEAD^{tree}").strip()
        }
        for entry in git(checkout, "ls-tree", "-r", "-t", "-z", "HEAD").split("\0"):
            if entry:
                metadata, path = entry.split("\t", 1)
                entries[path] = metadata
        used: dict[str, str] = {}
        visited: set[tuple[str, bool]] = set()

        def resolve(path: Path, seen: tuple[Path, ...] = ()) -> Path | None:
            path = Path(os.path.normpath(path))
            if not path.is_relative_to(checkout):
                raise CheckerError(f"Referenced path escapes submodule: {path}")
            if path in seen or len(seen) > 40:
                raise CheckerError(f"Symlink loop or too many symlinks: {path}")
            prefix = checkout
            for part in path.relative_to(checkout).parts:
                prefix /= part
                relative = prefix.relative_to(checkout).as_posix()
                metadata = entries.get(relative, "")
                if metadata.startswith("160000 "):
                    log.info("Skipping nested submodule reference %s", relative)
                    return None
                if metadata.startswith("120000 "):
                    used[relative] = metadata
                    target = prefix.parent / os.readlink(prefix)
                    return resolve(target / path.relative_to(prefix), (*seen, path))
            return path

        def visit(path: Path, manifest: bool) -> None:
            path = Path(os.path.normpath(path))
            key = (path.as_posix(), manifest)
            if key in visited:
                return
            visited.add(key)
            requested = path
            resolved = resolve(path)
            if resolved is None:
                return
            path = resolved
            relative = path.relative_to(checkout).as_posix()
            if relative not in entries:
                raise CheckerError(
                    f"Referenced file missing from submodule: {relative}"
                )
            used[relative] = entries[relative]
            if not manifest:
                if entries[relative].startswith("040000 "):
                    used[relative] = "040000 tree"
                    for child, metadata in entries.items():
                        if (
                            child != relative
                            and Path(child).is_relative_to(Path(relative))
                            and not metadata.startswith("040000 ")
                        ):
                            visit(checkout / child, False)
                return
            if not path.is_file():
                raise CheckerError(
                    f"Submodule manifest is not a regular file: {relative}"
                )
            if path.stat().st_size > self.max_manifest_size:
                raise CheckerError(f"Submodule manifest is too large: {relative}")
            try:
                contents = read_manifest(requested)
            except (GLib.Error, YAMLError, ValueError) as error:
                raise CheckerError(
                    f"Cannot read submodule manifest {relative}: {error}"
                ) from error
            if isinstance(contents, tuple):
                contents = contents[0]
            if not isinstance(contents, (dict, list)):
                raise CheckerError(
                    f"Submodule manifest must be an object or list: {relative}"
                )

            def walk(node: object) -> None:
                if isinstance(node, str):
                    visit(requested.parent / node, True)
                elif isinstance(node, list):
                    for child in node:
                        walk(child)
                elif isinstance(node, dict):
                    for local in local_source_paths(node):
                        visit(requested.parent / local, False)
                    for key in ("modules", "sources"):
                        if key in node:
                            walk(node[key])

            walk(contents)

        for path, manifest in references.items():
            visit(checkout / path, manifest)
        return used

    def _check_submodule(
        self,
        name: str,
        path: str,
        current: str,
        references: Mapping[Path, bool],
    ) -> SubmoduleUpdate | None:
        url = self._url(name)
        branch = self._config(name, "branch")
        if branch == ".":
            assert self.repository is not None
            branch = git(self.repository, "symbolic-ref", "--short", "HEAD").strip()
        with tempfile.TemporaryDirectory(prefix="fedc-submodule-") as tmp:
            checkout = Path(tmp) / "checkout"
            git(Path(tmp), "clone", "--no-checkout", "--", url, str(checkout))
            ref = (
                f"refs/remotes/origin/{branch}"
                if branch
                else "refs/remotes/origin/HEAD"
            )
            candidate = git(
                checkout, "rev-parse", "--verify", f"{ref}^{{commit}}"
            ).strip()
            if current == candidate:
                return None
            git(checkout, "checkout", "--detach", current)
            old = self._snapshot(checkout, references)
            git(checkout, "checkout", "--detach", candidate)
            new = self._snapshot(checkout, references)
        changed = tuple(
            sorted(
                file
                for file in old.keys() | new.keys()
                if old.get(file) != new.get(file)
            )
        )
        if not changed:
            return None
        return SubmoduleUpdate(path, url, current, candidate, changed)

    def check(self) -> None:
        self.reset()
        root = self.repository
        if root is None or not self.references or not (root / ".gitmodules").is_file():
            return
        try:
            paths = git(root, "config", "--null", "--file", ".gitmodules", "--list")
            index: dict[str, str] = {}
            for entry in git(root, "ls-files", "--stage", "-z").split("\0"):
                if entry.startswith("160000 "):
                    metadata, path = entry.split("\t", 1)
                    _, commit, stage = metadata.split()
                    if stage != "0":
                        raise CheckerError(f"Unmerged submodule: {path}")
                    index[path] = commit
            for record in paths.split("\0"):
                if not record:
                    continue
                key, _, path = record.partition("\n")
                if not (key.startswith("submodule.") and key.endswith(".path")):
                    continue
                base = root / path
                references: dict[Path, bool] = {}
                for reference, manifest in self.references.items():
                    relative = self._relative_reference(reference, base)
                    if relative is not None:
                        references[relative] = (
                            references.get(relative, False) or manifest
                        )
                if not references or path not in index:
                    continue
                try:
                    name = key[len("submodule.") : -len(".path")]
                    update = self._check_submodule(name, path, index[path], references)
                    if update is not None:
                        self.updates.append(update)
                except (CheckerError, OSError, subprocess.TimeoutExpired) as error:
                    self.errors.append(CheckerError(f"Submodule {path}: {error}"))
        except (CheckerError, OSError, subprocess.TimeoutExpired) as error:
            self.errors.append(error)

    def apply(self, update: SubmoduleUpdate) -> bool:
        root = self.repository
        assert root is not None
        path = root / update.path
        try:
            entry = git(root, "ls-files", "--stage", "--", update.path)
            if not entry.startswith(f"160000 {update.current} 0\t"):
                raise CheckerError(f"Submodule {update.path} changed since checking")
            git(root, "diff", "--quiet", "--cached", "--", update.path)
            if (path / ".git").exists():
                if git(path, "rev-parse", "HEAD").strip() != update.current or git(
                    path, "status", "--porcelain", "--untracked-files=all"
                ):
                    raise CheckerError(f"Submodule {update.path} has local changes")
            git(root, "submodule", "update", "--init", "--checkout", "--", update.path)
            git(path, "fetch", "--no-tags", "--", update.url, update.candidate)
            git(path, "checkout", "--no-overwrite-ignore", "--detach", update.candidate)
        except (CheckerError, OSError, subprocess.TimeoutExpired) as error:
            self.errors.append(
                CheckerError(f"Failed to update submodule {update.path}: {error}")
            )
            return False
        return True
