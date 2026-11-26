import dataclasses
import logging
import os
import posixpath
import re
import subprocess
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path

from gi.repository import GLib
from ruamel.yaml.error import YAMLError

from .errors import CheckerError
from .releaselinks import comparison_url
from .sourceupdates import SourceUpdate
from .utils import read_manifest

log = logging.getLogger(__name__)

MAX_SYMLINK_HOPS = 40


def local_source_paths(source: Mapping[str, object]) -> list[str]:
    paths = source.get("paths", [])
    return [
        path
        for path in [source.get("path"), *(paths if isinstance(paths, list) else [])]
        if isinstance(path, str)
    ]


def _exit_gitlink_by_parent_path(reference: Path, base: Path) -> Path | None:
    """Continue from an explicit parent step that leaves the gitlink."""
    parts = reference.relative_to(base).parts
    if ".." not in parts or base.resolve() != base:
        return None
    prefix = base
    for index, part in enumerate(parts):
        if part == "..":
            if prefix == base:
                return base.parent.joinpath(*parts[index + 1 :])
            prefix = prefix.parent
        else:
            prefix /= part
            if prefix.is_symlink():
                resolved = resolve_submodule_reference(prefix, base)
                if resolved is None:
                    return None
                prefix = resolved
    return None


def submodule_reference_path(
    reference: Path, base: Path, *, alias_parents: set[Path] | None = None
) -> Path | None:
    """Follow app aliases to the gitlink, retaining the path inside it."""
    # Follow aliases only as far as the gitlink. The pinned tree must still
    # validate any symlinks inside it.
    symlink_hops = 0
    while True:
        if reference.is_relative_to(base):
            outside = _exit_gitlink_by_parent_path(reference, base)
            if outside is None:
                return reference
            # An ordinary ".." can leave the gitlink and enter again by alias.
            reference = outside
            continue
        for ancestor in [*reversed(reference.parents), reference]:
            if ancestor.is_symlink():
                if symlink_hops == MAX_SYMLINK_HOPS:
                    return None
                symlink_hops += 1
                reference = Path(
                    ancestor.parent,
                    ancestor.readlink(),
                    reference.relative_to(ancestor),
                )
                if alias_parents is not None:
                    alias_parents.update(reference.parents)
                break
            resolved = ancestor.resolve()
            if resolved.is_relative_to(base):
                reference = Path(resolved, reference.relative_to(ancestor))
                break
        else:
            return None


def resolve_submodule_reference(reference: Path, base: Path) -> Path | None:
    """Resolve a live reference without leaving the gitlink or entering another repo."""
    symlink_hops = 0
    unmerged: set[Path] = set()
    inspected: set[Path] = set()
    while True:
        if not reference.is_relative_to(base):
            return None
        prefix = base
        parts = reference.relative_to(base).parts
        for index, part in enumerate(parts):
            if prefix not in inspected and (prefix / ".git").exists():
                inspected.add(prefix)
                try:
                    entries = git(prefix, "ls-files", "--unmerged", "-z")
                except (CheckerError, OSError, subprocess.TimeoutExpired):
                    return None
                unmerged.update(
                    prefix / path for path, _, _ in indexed_gitlinks(entries)
                )
            if part == "..":
                if prefix == base:
                    return None
                prefix = prefix.parent
                continue
            prefix /= part
            if prefix in unmerged:
                return None
            if prefix.is_symlink():
                if symlink_hops == MAX_SYMLINK_HOPS:
                    return None
                symlink_hops += 1
                target = prefix.parent / prefix.readlink()
                reference = target.joinpath(*parts[index + 1 :])
                break
            if (prefix / ".git").exists() and not is_nested_gitlink(base, prefix):
                return None
        else:
            return prefix


def indexed_gitlinks(entries: str) -> Iterator[tuple[str, str, str]]:
    """Yield (path, commit, stage) for each gitlink in ls-files --stage -z."""
    for entry in entries.split("\0"):
        if entry.startswith("160000 "):
            metadata, path = entry.split("\t", 1)
            _, commit, stage = metadata.split()
            yield path, commit, stage


def is_nested_gitlink(base: Path, path: Path) -> bool:
    """Check every repository boundary below base against its parent's gitlinks."""
    if not path.is_relative_to(base) or path == base:
        return False
    parent = base
    nested = base
    for part in path.relative_to(base).parts:
        nested /= part
        if not (nested / ".git").exists():
            continue
        relative = nested.relative_to(parent).as_posix()
        try:
            paths = configured_submodules(parent)
            if not any(configured == relative for _, configured in paths):
                return False
            entries = git(parent, "ls-files", "--stage", "-z", "--", relative)
        except (CheckerError, OSError, subprocess.TimeoutExpired):
            return False
        if not any(
            path == relative and stage == "0"
            for path, _, stage in indexed_gitlinks(entries)
        ):
            return False
        parent = nested
    return parent == path


def git_command(directory: Path, *args: str) -> list[str]:
    return [
        "git",
        "-c",
        f"safe.directory={directory.resolve()}",
        "-C",
        str(directory),
        *args,
    ]


def git(directory: Path, *args: str) -> str:
    result = subprocess.run(
        git_command(directory, "-c", "submodule.recurse=false", *args),
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        detail = os.fsdecode(result.stderr).strip()
        raise CheckerError(f"Git {args[0]} failed: {detail}")
    return os.fsdecode(result.stdout)


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
            submodule_files=self.files,
        )


def configured_submodules(root: Path) -> list[tuple[str, str]]:
    if not (root / ".gitmodules").is_file():
        return []
    config = git(root, "config", "--null", "--file", ".gitmodules", "--list")
    paths = []
    for record in config.split("\0"):
        key, _, path = record.partition("\n")
        if key.startswith("submodule.") and key.endswith(".path"):
            paths.append((key[len("submodule.") : -len(".path")], path))
    return paths


class SubmoduleChecker:
    def __init__(self, repository: Path | None, max_manifest_size: int) -> None:
        self.repository = repository
        self.max_manifest_size = max_manifest_size
        self.references: dict[Path, bool] = {}
        self.updates: list[SubmoduleUpdate] = []
        self.errors: list[Exception] = []

    def add_reference(self, path: str, *, manifest: bool) -> None:
        absolute = Path(path).absolute()
        self.references[absolute] = self.references.get(absolute, False) or manifest

    def reset(self) -> None:
        self.updates = []
        self.errors = []

    def _config(self, name: str, key: str) -> str:
        assert self.repository is not None
        for options in ((), ("--file", ".gitmodules")):
            result = subprocess.run(
                git_command(
                    self.repository,
                    "config",
                    *options,
                    "--get",
                    f"submodule.{name}.{key}",
                ),
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
        self,
        checkout: Path,
        references: list[tuple[Path, str | None]],
        *,
        validate_paths_only: bool = False,
    ) -> dict[str, str]:
        entries = {
            ".": "040000 tree " + git(checkout, "rev-parse", "HEAD^{tree}").strip()
        }
        for entry in git(checkout, "ls-tree", "-r", "-t", "-z", "HEAD").split("\0"):
            if entry:
                metadata, path = entry.split("\t", 1)
                entries[path] = metadata
        used: dict[str, str] = {}
        visited: set[tuple[Path, Path, str | None]] = set()

        def resolve(path: Path, seen: tuple[Path, ...] = ()) -> Path | None:
            if not path.is_relative_to(checkout):
                raise CheckerError(f"Referenced path escapes submodule: {path}")
            if path in seen or len(seen) > MAX_SYMLINK_HOPS:
                raise CheckerError(f"Symlink loop or too many symlinks: {path}")
            prefix = checkout
            parts = path.relative_to(checkout).parts
            for index, part in enumerate(parts):
                if part == "..":
                    if prefix == checkout:
                        raise CheckerError(f"Referenced path escapes submodule: {path}")
                    prefix = prefix.parent
                    continue
                prefix /= part
                relative = prefix.relative_to(checkout).as_posix()
                metadata = entries.get(relative, "")
                if metadata.startswith("160000 "):
                    if parts[index + 1 : index + 2] == ("..",):
                        continue
                    if ".." in parts[index + 1 :]:
                        raise CheckerError(
                            "Cannot validate parent traversal inside nested submodule: "
                            f"{relative}"
                        )
                    log.info("Skipping nested submodule reference %s", relative)
                    return None
                if metadata.startswith("120000 "):
                    used[relative] = metadata
                    target = prefix.parent / os.readlink(prefix)
                    resolved = resolve(
                        target.joinpath(*parts[index + 1 :]), (*seen, path)
                    )
                    if resolved is not None and not prefix.exists():
                        raise CheckerError(f"Broken symlink in submodule: {relative}")
                    return resolved
            return prefix

        def visit(path: Path, manifest_suffix: str | None) -> None:
            requested = path
            resolved = resolve(path)
            if resolved is None:
                return
            path = resolved
            key = (path, requested.parent.resolve(), manifest_suffix)
            if key in visited:
                return
            visited.add(key)
            relative = path.relative_to(checkout).as_posix()
            if relative not in entries:
                raise CheckerError(
                    f"Referenced file missing from submodule: {relative}"
                )
            used[relative] = entries[relative]
            if manifest_suffix is None:
                if not validate_paths_only and entries[relative].startswith("040000 "):
                    used[relative] = "040000 tree"
                    for child, metadata in entries.items():
                        if (
                            child != relative
                            and Path(child).is_relative_to(Path(relative))
                            and not metadata.startswith("040000 ")
                        ):
                            visit(checkout / child, None)
                return
            if not path.is_file():
                raise CheckerError(
                    f"Submodule manifest is not a regular file: {relative}"
                )
            if validate_paths_only:
                return
            if path.stat().st_size > self.max_manifest_size:
                raise CheckerError(f"Submodule manifest is too large: {relative}")
            try:
                contents = read_manifest(path, suffix=manifest_suffix)
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
                    visit(requested.parent / node, Path(node).suffix)
                elif isinstance(node, list):
                    for child in node:
                        walk(child)
                elif isinstance(node, dict):
                    for local in local_source_paths(node):
                        visit(requested.parent / local, None)
                    for key in ("modules", "sources"):
                        if key in node:
                            walk(node[key])

            walk(contents)

        for path, manifest_suffix in references:
            visit(checkout / path, manifest_suffix)
        return used

    def _check_submodule(
        self,
        name: str,
        path: str,
        current: str,
        references: list[tuple[Path, str | None]],
    ) -> SubmoduleUpdate | None:
        if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", current):
            raise CheckerError(f"Invalid submodule commit: {current}")
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
            try:
                git(checkout, "cat-file", "-e", f"{current}^{{commit}}")
            except CheckerError:
                git(checkout, "fetch", "--no-tags", "--depth=1", "--", url, current)
            git(checkout, "checkout", "--detach", current)
            if current == candidate:
                self._snapshot(checkout, references, validate_paths_only=True)
                return None
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
            paths = configured_submodules(root)
            index: dict[str, str] = {}
            for path, commit, stage in indexed_gitlinks(
                git(root, "ls-files", "--stage", "-z")
            ):
                if stage != "0":
                    raise CheckerError(f"Unmerged submodule: {path}")
                index[path] = commit
            for name, path in paths:
                base = root / path
                references: list[tuple[Path, str | None]] = []
                for reference, manifest in self.references.items():
                    tracked_reference = submodule_reference_path(reference, base)
                    # Explicit traversal out of the gitlink is not a dependency.
                    if tracked_reference is None:
                        continue
                    relative = tracked_reference.relative_to(base)
                    references.append(
                        (relative, reference.suffix if manifest else None)
                    )
                if not references or path not in index:
                    continue
                try:
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
