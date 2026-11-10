"""Identify the checker code, independently of the manifest checkout."""

import os
import re
import subprocess
from pathlib import Path

CHECKER_ROOT = Path(__file__).resolve().parents[2]


def checker_provenance():
    unknown = {"revision": None, "source": "unknown", "dirty": None}
    revision_file = CHECKER_ROOT / "build-revision"
    try:
        revision = revision_file.read_text().strip()
    except FileNotFoundError:
        pass
    except (OSError, UnicodeError):
        return unknown
    else:
        if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
            return {"revision": revision, "source": "build", "dirty": None}
        return unknown

    # An explicit git-dir also supports worktrees and prevents parent discovery.
    command = [
        "git",
        f"--git-dir={CHECKER_ROOT / '.git'}",
        f"--work-tree={CHECKER_ROOT}",
    ]
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args):
        return subprocess.check_output(
            [*command, *args],
            cwd=CHECKER_ROOT,
            env=environment,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()

    try:
        revision = git("rev-parse", "--verify", "HEAD")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", revision):
            return unknown
        dirty = bool(git("status", "--porcelain", "--untracked-files=normal"))
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return unknown
    return {"revision": revision, "source": "git", "dirty": dirty}
