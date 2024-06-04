import os
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

from src import main


TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__), "net.invisible_island.xterm.yml"
)
TEST_APPDATA = os.path.join(
    os.path.dirname(__file__), "net.invisible_island.xterm.appdata.xml"
)


@patch.dict(os.environ)
class TestEntrypoint(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._clear_environment()
        self.test_dir = tempfile.TemporaryDirectory()
        self.manifest_filename = os.path.basename(TEST_MANIFEST)
        self.appdata_filename = os.path.basename(TEST_APPDATA)
        self.manifest_path = os.path.join(self.test_dir.name, self.manifest_filename)
        self.appdata_path = os.path.join(self.test_dir.name, self.appdata_filename)
        self._run_cmd(["git", "init"])
        self._run_cmd(["git", "config", "user.name", "Test Runner"])
        self._run_cmd(["git", "config", "user.email", "test@localhost"])
        shutil.copy(TEST_MANIFEST, self.manifest_path)
        shutil.copy(TEST_APPDATA, self.appdata_path)
        self._run_cmd(["git", "add", self.manifest_filename])
        self._run_cmd(["git", "add", self.appdata_filename])
        self._run_cmd(["git", "commit", "-a", "-m", "Initial commit"])

    def tearDown(self):
        self.test_dir.cleanup()

    def _clear_environment(self):
        unwanted_vars = [
            "EMAIL",
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
        ]
        for var in unwanted_vars:
            os.environ.pop(var, None)

    def _run_cmd(self, cmd, **kwargs):
        return subprocess.run(cmd, cwd=self.test_dir.name, check=True, **kwargs)

    def _get_commit_data(self, rev="HEAD"):
        data = {}
        for name, fmt in [
            ("commit", "%H"),
            ("subject", "%s"),
            ("body", "%b"),
            ("author_name", "%an"),
            ("author_email", "%ae"),
            ("committer_name", "%cn"),
            ("committer_email", "%ce"),
        ]:
            cmd = ["git", "show", "--no-patch", f"--pretty=format:{fmt}", rev]
            proc = self._run_cmd(cmd, stdout=subprocess.PIPE)
            output = proc.stdout.decode("utf-8")
            data[name] = output

        return data

    async def test_full_run(self):
        args1 = main.parse_cli_args(["--update", "--commit-only", self.manifest_path])
        self.assertEqual(await main.run_with_args(args1), (2, 0, True))

        commit_data = self._get_commit_data()
        self.assertEqual(commit_data["subject"], "Update libXaw and xterm modules")
        self.assertEqual(commit_data["author_name"], "Test Runner")
        self.assertEqual(commit_data["author_email"], "test@localhost")
        self.assertEqual(commit_data["committer_name"], "Test Runner")
        self.assertEqual(commit_data["committer_email"], "test@localhost")

        body_lines = commit_data["body"].splitlines()
        self.assertEqual(len(body_lines), 2)
        self.assertRegex(body_lines[0], r"^libXaw: Update libXaw-1.0.12.tar.bz2 to ")
        self.assertRegex(body_lines[1], r"^xterm: Update xterm-snapshots.git to ")

        args2 = main.parse_cli_args([self.manifest_path])
        self.assertEqual(await main.run_with_args(args2), (0, 0, False))

    async def test_git_envvars(self):
        os.environ["GIT_AUTHOR_NAME"] = "Some Guy"
        os.environ["GIT_AUTHOR_EMAIL"] = "someguy@localhost"
        args1 = main.parse_cli_args(["--update", "--commit-only", self.manifest_path])
        self.assertEqual(await main.run_with_args(args1), (2, 0, True))

        commit_data = self._get_commit_data()
        self.assertEqual(commit_data["subject"], "Update libXaw and xterm modules")
        self.assertEqual(commit_data["author_name"], "Some Guy")
        self.assertEqual(commit_data["author_email"], "someguy@localhost")
        self.assertEqual(commit_data["committer_name"], "Test Runner")
        self.assertEqual(commit_data["committer_email"], "test@localhost")


class TestForceForkTristate(unittest.TestCase):
    def test_neither_fork_arg(self):
        args = main.parse_cli_args([TEST_MANIFEST])
        self.assertIsNone(args.fork)

    def test_always_fork_arg(self):
        args = main.parse_cli_args(["--always-fork", TEST_MANIFEST])
        self.assertTrue(args.fork)

    def test_never_fork_arg(self):
        args = main.parse_cli_args(["--never-fork", TEST_MANIFEST])
        self.assertFalse(args.fork)

    def test_both_fork_args(self):
        with self.assertRaises(SystemExit):
            main.parse_cli_args(["--always-fork", "--never-fork", TEST_MANIFEST])
