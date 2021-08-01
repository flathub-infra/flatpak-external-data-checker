import os
import subprocess
import shutil
import tempfile
import unittest

from src import main


TEST_MANIFEST = os.path.join(
    os.path.dirname(__file__), "net.invisible_island.xterm.yml"
)
TEST_APPDATA = os.path.join(
    os.path.dirname(__file__), "net.invisible_island.xterm.appdata.xml"
)


class TestEntrypoint(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
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

    def _run_cmd(self, cmd):
        return subprocess.run(cmd, cwd=self.test_dir.name, check=True)

    async def test_full_run(self):
        args1 = main.parse_cli_args(["--update", "--commit-only", self.manifest_path])
        self.assertEqual(await main.run_with_args(args1), (2, 0, True))
        args2 = main.parse_cli_args([self.manifest_path])
        self.assertEqual(await main.run_with_args(args2), (0, 0, False))
