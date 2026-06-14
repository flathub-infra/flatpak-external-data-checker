import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src import main
from src.lib.externaldata import ExternalData

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

    async def test_branch_exists(self):
        current_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=self.test_dir.name, text=True
        ).strip()
        with main.indir(self.test_dir.name):
            self.assertTrue(main.branch_exists(current_branch))
            self.assertFalse(main.branch_exists("nonexistent-branch"))

    async def test_commit_to_base_branch(self):
        base_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], cwd=self.test_dir.name, text=True
        ).strip()

        args = main.parse_cli_args(
            [
                "--update",
                "--commit-only",
                "--commit-to-current-branch",
                self.manifest_path,
            ]
        )

        with patch("src.main.open_pr") as mock_open_pr:
            result = await main.run_with_args(args)
            self.assertEqual(result, (2, 0, True))
            mock_open_pr.assert_not_called()

        branch_name = self._run_cmd(
            ["git", "branch", "--show-current"], stdout=subprocess.PIPE
        )
        self.assertEqual(branch_name.stdout.decode().strip(), base_branch)

        commit_msg_proc = self._run_cmd(
            ["git", "log", "-1", "--pretty=%s"], stdout=subprocess.PIPE
        )
        commit_msg = commit_msg_proc.stdout.decode().strip()
        self.assertEqual(commit_msg, "Update libXaw and xterm modules")

    async def test_commit_to_base_no_base_branch(self):
        self._run_cmd(["git", "checkout", "--detach"])

        args = main.parse_cli_args(
            [
                "--update",
                "--commit-only",
                "--commit-to-current-branch",
                self.manifest_path,
            ]
        )

        with patch("src.main.open_pr") as mock_open_pr:
            result = await main.run_with_args(args)
            self.assertEqual(result, (-1, -1, False))
            mock_open_pr.assert_not_called()


class TestCommitFallback(unittest.TestCase):
    def setUp(self):
        self._clear_environment()
        self.test_dir = tempfile.TemporaryDirectory()
        self.manifest_filename = "test.yaml"
        self.manifest_path = os.path.join(self.test_dir.name, self.manifest_filename)

        with open(self.manifest_path, "w") as f:
            f.write("test: content\n")

        self._run(["git", "init"])
        self._run(["git", "add", self.manifest_filename])
        self._run(
            [
                "git",
                "-c",
                "user.name=testuser",
                "-c",
                "user.email=testuser@localhost",
                "commit",
                "-m",
                "Initial commit",
            ]
        )

        with open(self.manifest_path, "a") as f:
            f.write("new: change\n")
        self._run(["git", "add", self.manifest_filename])

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

    def _run(self, cmd, **kwargs):
        subprocess.run(cmd, cwd=self.test_dir.name, check=True, **kwargs)

    def _git_show(self, fmt, rev="HEAD"):
        cmd = ["git", "show", "--no-patch", f"--pretty=format:{fmt}", rev]
        proc = subprocess.run(
            cmd, cwd=self.test_dir.name, capture_output=True, check=True
        )
        return proc.stdout.decode().strip()

    @patch("getpass.getuser", return_value="fallbackuser")
    @patch("src.main.check_call")
    def test_commit_changes_fallback_1(self, mock_check_call, mock_getuser):

        def mock_check_call_side_effect(cmd, **kwargs):
            if isinstance(cmd, list) and "commit" in cmd and "-am" in cmd:
                raise subprocess.CalledProcessError(1, cmd)
            return subprocess.check_call(cmd)

        mock_check_call.side_effect = mock_check_call_side_effect

        with main.indir(self.test_dir.name):
            main.commit_changes(["Some commit"])

        author_name = self._git_show("%an")
        author_email = self._git_show("%ae")
        subject = self._git_show("%s")
        self.assertEqual(author_name, "fallbackuser")
        self.assertEqual(author_email, "fallbackuser@localhost")
        self.assertEqual(subject, "Some commit")

    @patch("getpass.getuser", return_value="fallbackuser")
    def test_commit_changes_fallback_2(self, mock_getuser):

        hooks_dir = os.path.join(self.test_dir.name, ".git", "hooks")
        os.makedirs(hooks_dir, exist_ok=True)
        hook_path = os.path.join(hooks_dir, "pre-commit")

        with open(hook_path, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(hook_path, 0o755)

        with main.indir(self.test_dir.name):
            main.commit_changes(["Some commit"])

        author_name = self._git_show("%an")
        author_email = self._git_show("%ae")
        subject = self._git_show("%s")
        self.assertEqual(author_name, "fallbackuser")
        self.assertEqual(author_email, "fallbackuser@localhost")
        self.assertEqual(subject, "Some commit")


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


class TestRobotsTxtArg(unittest.TestCase):
    def test_enable_robots_txt_flag(self):
        with self.subTest("default is False"):
            args = main.parse_cli_args([TEST_MANIFEST])
            self.assertFalse(args.enable_robots_txt)

        with self.subTest("flag sets True"):
            args = main.parse_cli_args(["--enable-robots-txt", TEST_MANIFEST])
            self.assertTrue(args.enable_robots_txt)


class TestPrintOutdatedBrokenState(unittest.TestCase):
    def _make_broken_data(self):
        data = MagicMock()
        data.state = MagicMock()
        data.state.name = "BROKEN"
        data.State.BROKEN.__contains__ = MagicMock(return_value=True)
        data.new_version = None
        data.filename = "somelib.tar.gz"
        data.current_version._asdict.return_value = {
            "url": "https://example.com/somelib.tar.gz"
        }
        return data

    def test_broken_state_no_new_version(self):
        checker = MagicMock()
        broken = self._make_broken_data()
        broken.State.BROKEN.__ror__ = MagicMock(return_value=True)
        checker.get_outdated_external_data.return_value = [broken]

        with patch("builtins.print"):
            result = main.print_outdated_external_data(checker)
        self.assertEqual(result, 1)

    def test_broken_state_message_args(self):
        checker = MagicMock()

        broken = MagicMock()
        broken.new_version = None
        broken.filename = "somelib.tar.gz"
        broken.state = ExternalData.State.BROKEN
        broken.State = ExternalData.State
        broken.current_version._asdict.return_value = {
            "url": "https://example.com/somelib.tar.gz"
        }

        checker.get_outdated_external_data.return_value = [broken]

        with patch("builtins.print"):
            result = main.print_outdated_external_data(checker)

        self.assertEqual(result, 1)
        broken.current_version._asdict.assert_called_once()


class TestGetManifestGitCheckout(unittest.TestCase):
    def test_no_git_checkout_raises(self):
        with tempfile.TemporaryDirectory() as d:
            fake_manifest = os.path.join(d, "manifest.yml")
            with self.assertRaises(FileNotFoundError):
                main.get_manifest_git_checkout(fake_manifest)


class TestEnsureGitSafeDirectory(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.test_dir.cleanup()

    def test_same_uid_returns_early(self):
        checkout = Path(self.test_dir.name)
        uid = os.getuid()
        with patch("os.stat") as mock_stat:
            mock_stat.return_value.st_uid = uid
            main.ensure_git_safe_directory(checkout)

    def test_different_uid_no_safe_dir_adds(self):
        checkout = Path(self.test_dir.name)
        with (
            patch("os.getuid", return_value=1000),
            patch("os.stat") as mock_stat,
            patch("subprocess.run") as mock_run,
            patch("src.main.check_call") as mock_check_call,
        ):
            mock_stat.return_value.st_uid = 9999
            err = subprocess.CalledProcessError(1, "git")
            mock_run.side_effect = err
            main.ensure_git_safe_directory(checkout)
            mock_check_call.assert_called_once()
            args = mock_check_call.call_args[0][0]
            self.assertIn("safe.directory", args)

    def test_different_uid__reraises(self):
        checkout = Path(self.test_dir.name)
        with (
            patch("os.getuid", return_value=1000),
            patch("os.stat") as mock_stat,
            patch("subprocess.run") as mock_run,
        ):
            mock_stat.return_value.st_uid = 9999
            err = subprocess.CalledProcessError(2, "git")
            mock_run.side_effect = err
            with self.assertRaises(subprocess.CalledProcessError):
                main.ensure_git_safe_directory(checkout)

    def test_different_uid_already_safe(self):
        checkout = Path(self.test_dir.name)
        with (
            patch("os.getuid", return_value=1000),
            patch("os.stat") as mock_stat,
            patch("subprocess.run") as mock_run,
            patch("src.main.check_call") as mock_check_call,
        ):
            mock_stat.return_value.st_uid = 9999
            result = MagicMock()
            result.stdout = str(checkout) + "\n"
            mock_run.return_value = result
            main.ensure_git_safe_directory(checkout)
            mock_check_call.assert_not_called()


class TestCommitMessage(unittest.TestCase):
    def test_single_change(self):
        self.assertEqual(
            main.commit_message(["foo: Update foo-1.0"]), "foo: Update foo-1.0"
        )

    def test_single_module_multiple_changes(self):
        result = main.commit_message(["foo: change 1", "foo: change 2"])
        self.assertEqual(result, "Update foo module")

    def test_two_modules(self):
        result = main.commit_message(["foo: change", "bar: change"])
        self.assertIn("foo", result)
        self.assertIn("bar", result)

    def test_subject_truncation(self):
        changes = [f"module{i}: change" for i in range(10)]
        result = main.commit_message(changes)
        self.assertLessEqual(len(result), 70)

    def test_suffix(self):
        changes = [f"module{i}: change" for i in range(10)]
        result = main.commit_message(changes)
        self.assertIn("module", result)

    def test_three_modules(self):
        changes = ["aaa: c", "bbb: c", "ccc: c", "ddd: c"]
        result = main.commit_message(changes)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    def test_commit_message_long(self):
        long_name = "a" * 35
        changes = [f"{long_name}{i}: change" for i in range(10)]
        result = main.commit_message(changes)
        self.assertEqual(result, "Update 10 modules")


class TestOpenPR(unittest.TestCase):
    def _make_change(self):
        return main.CommittedChanges(
            subject="Update foo",
            body="foo: update details",
            commit="abc1234",
            branch="update-abc1234",
            base_branch="main",
        )

    def _setup_mocks(
        self, mock_github, open_prs=None, closed_prs=None, push_permission=True
    ):
        g = MagicMock()
        mock_github.return_value = g
        user = MagicMock()
        g.get_user.return_value = user

        origin_repo = MagicMock()
        origin_repo.permissions.push = push_permission
        origin_repo.default_branch = "main"
        origin_repo.full_name = "owner/repo"
        origin_repo.html_url = "https://github.com/owner/repo"
        g.get_repo.return_value = origin_repo

        all_prs = (closed_prs or []) + (open_prs or [])
        origin_repo.get_pulls.return_value = all_prs

        return g, user, origin_repo

    def test_missing_github_token_exits(self):
        change = main.CommittedChanges(
            subject="Update foo",
            body=None,
            commit="abc1234",
            branch="update-abc1234",
            base_branch="main",
        )
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GITHUB_TOKEN", None)
            with self.assertRaises(SystemExit):
                main.open_pr(change)

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.Github")
    def test_closed_pr_returns_early(self, mock_github, mock_check_output):
        closed_pr = MagicMock()
        closed_pr.state = "closed"
        closed_pr.is_merged.return_value = False
        closed_pr.html_url = "https://github.com/owner/repo/pull/1"

        self._setup_mocks(mock_github, closed_prs=[closed_pr])

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change())

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_no_existing_prs_creates_pr(
        self, mock_github, mock_check_call, mock_check_output
    ):
        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[], closed_prs=[])
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/2"
        origin_repo.create_pull.return_value = pr

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change(), pr_labels=["label1"])

        origin_repo.create_pull.assert_called_once()
        pr.set_labels.assert_called_once_with("label1")

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_open_pr_automerge_config(
        self, mock_github, mock_check_call, mock_check_output
    ):
        open_pr = MagicMock()
        open_pr.state = "open"
        open_pr.html_url = "https://github.com/owner/repo/pull/3"
        open_pr.mergeable = True
        pr_commit = MagicMock()
        pr_commit.get_combined_status.return_value.state = "success"
        open_pr.head.repo.get_commit.return_value = pr_commit

        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[open_pr])

        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
            patch(
                "builtins.open",
                unittest.mock.mock_open(read_data='{"automerge-flathubbot-prs": true}'),
            ),
        ):
            main.open_pr(self._make_change())

        open_pr.merge.assert_called_once_with(merge_method="rebase")
        open_pr.create_issue_comment.assert_called_once_with(
            main.AUTOMERGE_DUE_TO_CONFIG
        )

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_open_pr_force_automerge_broken_urls(
        self, mock_github, mock_check_call, mock_check_output
    ):
        open_pr_mock = MagicMock()
        open_pr_mock.state = "open"
        open_pr_mock.html_url = "https://github.com/owner/repo/pull/4"
        open_pr_mock.mergeable = True
        pr_commit = MagicMock()
        pr_commit.get_combined_status.return_value.state = "success"
        open_pr_mock.head.repo.get_commit.return_value = pr_commit

        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[open_pr_mock])

        broken_data = MagicMock()
        broken_data.Type = ExternalData.Type
        broken_data.State = ExternalData.State
        broken_data.type = ExternalData.Type.EXTRA_DATA
        broken_data.state = [ExternalData.State.BROKEN]
        broken_data.new_version = MagicMock()

        checker = MagicMock()
        checker.get_outdated_external_data.return_value = [broken_data]

        with (
            patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}),
            patch("builtins.open", side_effect=FileNotFoundError),
        ):
            main.open_pr(self._make_change(), manifest_checker=checker)

            open_pr_mock.create_issue_comment.assert_called_once_with(
                main.AUTOMERGE_DUE_TO_BROKEN_URLS
            )
            open_pr_mock.merge.assert_called_once_with(merge_method="rebase")

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_always_fork(self, mock_github, mock_check_call, mock_check_output):
        _, user, origin_repo = self._setup_mocks(
            mock_github, open_prs=[], closed_prs=[]
        )
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/5"
        origin_repo.create_pull.return_value = pr

        fork_repo = MagicMock()
        fork_repo.full_name = "forkowner/repo"
        fork_repo.owner.login = "forkowner"
        user.create_fork.return_value = fork_repo

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change(), fork=True)

        user.create_fork.assert_called_once_with(origin_repo)

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_never_fork(self, mock_github, mock_check_call, mock_check_output):
        _, user, origin_repo = self._setup_mocks(
            mock_github, open_prs=[], closed_prs=[], push_permission=False
        )
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/6"
        origin_repo.create_pull.return_value = pr

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change(), fork=False)

        user.create_fork.assert_not_called()

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_non_default_base_branch_prefixes_subject(
        self, mock_github, mock_check_call, mock_check_output
    ):
        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[], closed_prs=[])
        origin_repo.default_branch = "main"
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/7"
        origin_repo.create_pull.return_value = pr

        change = main.CommittedChanges(
            subject="Update foo",
            body=None,
            commit="abc1234",
            branch="update-abc1234",
            base_branch="stable-1.0",
        )

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(change)

        call_kwargs = origin_repo.create_pull.call_args[1]
        self.assertTrue(call_kwargs["title"].startswith("[stable-1.0]"))

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_github_run_id_appends_log_url(
        self, mock_github, mock_check_call, mock_check_output
    ):
        _, _, origin_repo = self._setup_mocks(mock_github, open_prs=[], closed_prs=[])
        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/8"
        origin_repo.create_pull.return_value = pr

        with patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "fake-token",
                "GITHUB_RUN_ID": "12345",
                "GITHUB_REPOSITORY": "owner/repo",
            },
        ):
            main.open_pr(self._make_change())

        call_kwargs = origin_repo.create_pull.call_args[1]
        self.assertIn("actions/runs/12345", call_kwargs["body"])

    @patch("subprocess.check_output", return_value=b"https://github.com/owner/repo\n")
    @patch("src.main.check_call")
    @patch("src.main.Github")
    def test_no_push_permission_creates_fork(
        self, mock_github, mock_check_call, mock_check_output
    ):
        _, user, origin_repo = self._setup_mocks(
            mock_github, open_prs=[], closed_prs=[], push_permission=False
        )
        fork_repo = MagicMock()
        fork_repo.full_name = "forkowner/repo"
        fork_repo.owner.login = "forkowner"
        user.create_fork.return_value = fork_repo

        pr = MagicMock()
        pr.html_url = "https://github.com/owner/repo/pull/99"
        origin_repo.create_pull.return_value = pr

        with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token"}):
            main.open_pr(self._make_change(), fork=None)

        user.create_fork.assert_called_once_with(origin_repo)


class TestMain(unittest.IsolatedAsyncioTestCase):
    @patch("src.main.asyncio")
    @patch("src.main.parse_cli_args")
    def test_main_error_result(self, mock_parse, mock_asyncio):
        mock_parse.return_value = MagicMock(check_outdated=False)
        mock_asyncio.run.return_value = (-1, -1, False)
        with self.assertRaises(SystemExit) as ctx:
            main.main()
        self.assertTrue(int(ctx.exception.code) & int(main.ResultCode.ERROR))

    @patch("src.main.asyncio")
    @patch("src.main.parse_cli_args")
    def test_main_outdated_not_updated(self, mock_parse, mock_asyncio):
        args = MagicMock(check_outdated=True)
        mock_parse.return_value = args
        mock_asyncio.run.return_value = (3, 0, False)
        with self.assertRaises(SystemExit) as ctx:
            main.main()
        self.assertTrue(int(ctx.exception.code) & int(main.ResultCode.OUTDATED))

    @patch("src.main.asyncio")
    @patch("src.main.parse_cli_args")
    def test_main_success(self, mock_parse, mock_asyncio):
        args = MagicMock(check_outdated=False)
        mock_parse.return_value = args
        mock_asyncio.run.return_value = (0, 0, False)
        with self.assertRaises(SystemExit) as ctx:
            main.main()
        self.assertEqual(int(ctx.exception.code), int(main.ResultCode.SUCCESS))

    @patch("src.main.asyncio")
    @patch("src.main.parse_cli_args")
    def test_main_errors_num_sets_error(self, mock_parse, mock_asyncio):
        args = MagicMock(check_outdated=False)
        mock_parse.return_value = args
        mock_asyncio.run.return_value = (0, 2, False)
        with self.assertRaises(SystemExit) as ctx:
            main.main()
        self.assertTrue(int(ctx.exception.code) & int(main.ResultCode.ERROR))

    @patch("src.main.open_pr")
    @patch("src.main.commit_changes")
    @patch("src.main.ensure_git_safe_directory")
    @patch("src.main.get_manifest_git_checkout")
    @patch("src.main.indir")
    async def test_run_with_args_calls_open_pr(
        self, mock_indir, mock_checkout, mock_safe, mock_commit, mock_open_pr
    ):
        mock_indir.return_value = MagicMock(
            __enter__=lambda s: s, __exit__=MagicMock(return_value=False)
        )
        mock_checkout.return_value = Path("/fake/checkout")
        committed = main.CommittedChanges(
            subject="Update foo",
            body=None,
            commit="abc123",
            branch="update-abc123",
            base_branch="main",
        )
        mock_commit.return_value = committed

        with patch("src.main.manifest.ManifestChecker") as MockChecker:
            checker = MockChecker.return_value
            checker.check = AsyncMock(return_value=None)

            mock_data = MagicMock()

            mock_data.__class__ = ExternalData

            mock_data.type = ExternalData.Type.EXTRA_DATA
            mock_data.State = ExternalData.State

            mock_data.state = MagicMock()
            mock_data.state.name = "UNKNOWN"

            mock_data.filename = "foo.tar.gz"

            mock_data.new_version._asdict.return_value = {
                "url": "https://example.com/foo.tar.gz",
                "version": "1.0",
                "timestamp": "now",
            }
            mock_data.new_version.checksum._asdict.return_value = {
                "md5": "1",
                "sha1": "2",
                "sha256": "3",
                "sha512": "4",
                "size": 100,
            }

            checker.get_outdated_external_data.return_value = [mock_data]

            checker.update_manifests.return_value = ["foo: update"]
            checker.get_errors.return_value = []

            args = main.parse_cli_args(["--update", "/fake/manifest.yml"])
            args.commit_only = False
            args.commit_to_current_branch = False

            with patch("builtins.print"):
                result = await main.run_with_args(args)

        mock_open_pr.assert_called_once()
        self.assertEqual(result[2], True)


if __name__ == "__main__":
    unittest.main()
