import asyncio
import tempfile
import threading
from dataclasses import asdict, dataclass

import gi

gi.require_version("Flatpak", "1.0")
from gi.repository import Flatpak, Gio  # noqa: E402


@dataclass(frozen=True)
class RuntimeStatus:
    ref: str
    end_of_life: str | None
    end_of_life_rebase: str | None

    @property
    def is_eol(self) -> bool:
        return self.end_of_life is not None or self.end_of_life_rebase is not None


def fetch_runtime_statuses() -> dict[tuple[str, str], list[RuntimeStatus]]:
    cancellable = Gio.Cancellable.new()
    timer = threading.Timer(60, cancellable.cancel)
    timer.start()
    try:
        with tempfile.TemporaryDirectory(prefix="fedc-flatpak-") as directory:
            installation = Flatpak.Installation.new_for_path(
                Gio.File.new_for_path(directory), True, cancellable
            )
            remote = Flatpak.Remote.new("flathub")
            remote.set_url("https://dl.flathub.org/repo/")
            # Only read metadata over HTTPS; no artifacts are installed.
            remote.set_gpg_verify(False)
            installation.add_remote(remote, True, cancellable)
            runtimes: dict[tuple[str, str], list[RuntimeStatus]] = {}
            refs = installation.list_remote_refs_sync_full(
                "flathub",
                Flatpak.QueryFlags.ALL_ARCHES,
                cancellable,
            )
            for ref in refs:
                if ref.get_kind() != Flatpak.RefKind.RUNTIME:
                    continue
                runtimes.setdefault((ref.get_name(), ref.get_branch()), []).append(
                    RuntimeStatus(ref.format_ref(), ref.get_eol(), ref.get_eol_rebase())
                )
            return runtimes
    finally:
        timer.cancel()


class RuntimeEolChecker:
    def __init__(self):
        self._runtimes: dict[tuple[str, str], list[RuntimeStatus]] | None = None

    async def check(self, contents: dict) -> list[dict] | None:
        if contents.get("build-extension") is not True:
            return None
        runtime = contents.get("runtime")
        version = contents.get("runtime-version")
        if not isinstance(runtime, str) or not runtime:
            raise ValueError("Extension manifest must specify a runtime")
        if not isinstance(version, str) or not version:
            raise ValueError("Extension manifest must specify a runtime-version")
        if self._runtimes is None:
            self._runtimes = await asyncio.to_thread(fetch_runtime_statuses)
        parents = self._runtimes.get((runtime, version))
        if not parents:
            raise ValueError(f"Runtime {runtime}//{version} was not found on Flathub")
        # Do not retire a branch that still has an active published architecture.
        if not all(parent.is_eol for parent in parents):
            return None
        return [asdict(parent) for parent in parents]
