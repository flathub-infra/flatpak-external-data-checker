import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.lib.provenance import checker_provenance


class TestProvenance(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.root_patch = patch("src.lib.provenance.CHECKER_ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], stderr=subprocess.DEVNULL, text=True
        ).strip()

    def initialize(self):
        self.git("init")
        self.git(
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "Initial",
        )
        return self.git("rev-parse", "HEAD")

    def test_local_checkout_and_dirty_state(self):
        revision = self.initialize()
        # Neither the manifest cwd nor Git environment may select another repo.
        with patch.dict(os.environ, {"GIT_DIR": "/nonexistent"}):
            self.assertEqual(
                checker_provenance(),
                {"revision": revision, "source": "git", "dirty": False},
            )
        (self.root / "changed.py").write_text("changed")
        self.assertTrue(checker_provenance()["dirty"])
        self.git("add", "changed.py")
        self.assertTrue(checker_provenance()["dirty"])

    def test_invalid_build_encoding_is_unknown(self):
        (self.root / "build-revision").write_bytes(b"\xff")
        self.assertEqual(checker_provenance()["source"], "unknown")

    def test_invalid_git_output_encoding_is_unknown(self):
        self.initialize()
        self.git("config", "core.quotePath", "false")
        filename = os.fsencode(self.root) + b"/file-\xff"
        with open(filename, "wb"):
            pass
        self.assertEqual(
            checker_provenance(),
            {"revision": None, "source": "unknown", "dirty": None},
        )

    def test_worktree(self):
        revision = self.initialize()
        worktree = self.root / "worktree"
        self.git("worktree", "add", "--detach", str(worktree))
        with patch("src.lib.provenance.CHECKER_ROOT", worktree):
            self.assertEqual(
                checker_provenance(),
                {"revision": revision, "source": "git", "dirty": False},
            )

    def test_build_revision_without_git(self):
        revision = "a" * 40
        (self.root / "build-revision").write_text(revision + "\n")
        with patch("src.lib.provenance.subprocess.check_output") as git:
            self.assertEqual(
                checker_provenance(),
                {"revision": revision, "source": "build", "dirty": None},
            )
            git.assert_not_called()

    def test_unknown_build_does_not_fall_back_to_other_checkout(self):
        self.initialize()
        for revision in ("unknown", "", "latest", "abc123", "a" * 40 + "\nextra"):
            with self.subTest(revision=revision):
                (self.root / "build-revision").write_text(revision)
                self.assertEqual(
                    checker_provenance(),
                    {"revision": None, "source": "unknown", "dirty": None},
                )

    def test_archive_does_not_discover_parent_repository(self):
        self.initialize()
        archive = self.root / "archive"
        archive.mkdir()
        with patch("src.lib.provenance.CHECKER_ROOT", archive):
            self.assertEqual(checker_provenance()["source"], "unknown")

    def test_git_failures_are_unknown(self):
        for error in (
            FileNotFoundError(),
            subprocess.TimeoutExpired("git", 5),
            subprocess.CalledProcessError(1, "git"),
        ):
            with self.subTest(error=error):
                with patch(
                    "src.lib.provenance.subprocess.check_output", side_effect=error
                ):
                    self.assertEqual(
                        checker_provenance(),
                        {"revision": None, "source": "unknown", "dirty": None},
                    )
