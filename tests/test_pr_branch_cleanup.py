import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, call, mock_open, patch

from github import GithubException, UnknownObjectException

from src import main


class TestPRBranchCleanup(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.github = stack.enter_context(patch("src.main.Github"))
        stack.enter_context(patch.dict("os.environ", {"GITHUB_TOKEN": "test-token"}))
        stack.enter_context(
            patch(
                "src.main.subprocess.check_output",
                return_value=b"https://github.com/flathub/com.github.marhkb.Pods\n",
            )
        )
        stack.enter_context(
            patch(
                "builtins.open",
                mock_open(read_data='{"automerge-flathubbot-prs": true}'),
            )
        )
        self.push = stack.enter_context(patch("src.main.check_call"))
        self.git = stack.enter_context(patch("src.main.subprocess.run"))
        self.git.return_value.returncode = 0
        self.repo = self.github.return_value.get_repo.return_value
        self.repo.default_branch = "master"
        self.repo.owner.login = "flathub"
        self.pr = MagicMock()
        self.pr.state = "open"
        self.pr.html_url = "https://github.com/flathub/com.github.marhkb.Pods/pull/30"
        self.pr.head.ref = "update-master-cc7de96"
        self.pr.head.sha = "a" * 40
        self.pr.head.repo = self.repo
        self.pr.base.ref = "master"
        self.pr.body = f"{main.DISCLAIMER}\n\n<!-- fedc-head: {self.pr.head.sha} -->"
        self.pr.commits = 1
        self.repo.get_commit.return_value.commit.tree.sha = "cc7de96"
        self.pr.mergeable = True
        commit = self.pr.head.repo.get_commit.return_value
        commit.get_combined_status.return_value.state = "success"
        self.pr.merge.return_value.merged = True
        self.repo.get_pulls.return_value = [self.pr]
        self.ref = self.repo.get_git_ref.return_value
        self.ref.object.sha = self.pr.head.sha
        self.change = main.CommittedChanges(
            subject="Update Pods",
            body="",
            commit="cc7de96",
            branch=self.pr.head.ref,
            base_branch="master",
        )

    def open_pr(self):
        report = {}
        self.assertTrue(main.open_pr(self.change, fork=False, report=report))
        self.assertEqual(
            report["pull_request"], {"url": self.pr.html_url, "outcome": "merged"}
        )
        self.pr.merge.assert_called_once_with(
            merge_method="rebase", sha=self.pr.head.sha
        )
        self.repo.get_git_ref.assert_called_once_with("heads/update-master-cc7de96")
        self.repo.create_pull.assert_not_called()
        self.push.assert_not_called()

    def test_successful_cleanup(self):
        self.open_pr()
        self.assertIn(
            f"--force-with-lease=refs/heads/{self.change.branch}:{self.pr.head.sha}",
            self.git.call_args.args[0],
        )
        self.pr.assert_has_calls(
            [
                call.merge(merge_method="rebase", sha=self.pr.head.sha),
                call.create_issue_comment(main.AUTOMERGE_DUE_TO_CONFIG),
            ]
        )

    def test_merge_deletes_fork_branch_not_same_named_origin_branch(self):
        fork = self.repo.create_fork.return_value
        fork.full_name = "bot/app"
        fork.get_commit.return_value.commit.tree.sha = self.change.commit
        fork.get_git_ref.return_value.object.sha = self.pr.head.sha
        self.pr.head.repo = fork
        fork.get_commit.return_value.get_combined_status.return_value.state = "success"
        main.open_pr(self.change, fork=True)
        fork.get_git_ref.assert_called_once_with(f"heads/{self.change.branch}")
        self.assertIn(
            "https://x-access-token:test-token@github.com/bot/app",
            self.git.call_args.args[0],
        )
        self.repo.get_git_ref.assert_not_called()

    def setup_declined_fork(self):
        fork = self.repo.create_fork.return_value
        fork.default_branch = "master"
        fork.full_name = "update-bot/app"
        fork.owner.login = "update-bot"
        self.pr.state = "closed"
        self.pr.is_merged.return_value = False
        self.pr.body = f"{main.DISCLAIMER}\n\n<!-- fedc-head: {self.pr.head.sha} -->"
        self.pr.commits = 1
        self.pr.base.repo = self.repo
        self.pr.base.ref = self.change.base_branch
        self.pr.head.repo = fork
        self.pr.head.sha = "a" * 40
        ref = MagicMock(ref=f"refs/heads/{self.change.branch}")
        ref.object.sha = self.pr.head.sha
        fork.get_git_matching_refs.return_value = [ref]
        fork.get_commit.return_value.commit.tree.sha = self.change.commit
        self.repo.get_pulls.side_effect = lambda *, state, head=None, base=None: (
            [self.pr]
            if state == "all" and head == f"update-bot:{self.change.branch}"
            else []
        )
        return fork

    def test_declined_update_cleanup_preserves_pr_history(self):
        push = self.git
        fork = self.setup_declined_fork()
        push.return_value.returncode = 0
        main._cleanup_closed_branches(self.repo, fork, "test-token", "master")
        ref = f"refs/heads/{self.change.branch}"
        push.assert_called_once_with(
            [
                "git",
                "push",
                f"--force-with-lease={ref}:{self.pr.head.sha}",
                f"https://x-access-token:test-token@github.com/{fork.full_name}",
                f":{ref}",
            ],
            capture_output=True,
            check=False,
        )
        self.repo.get_git_ref.assert_not_called()
        fork.get_git_matching_refs.return_value = []
        report = {}
        main.open_pr(self.change, fork=True, report=report)
        self.assertEqual(report["pull_request"]["outcome"], "existing_closed")
        self.repo.create_pull.assert_not_called()
        self.assertEqual(push.call_count, 1)

        new = self.change._replace(branch="update-master-abcdef0", commit="abcdef0")
        main.open_pr(new, fork=True)
        self.repo.create_pull.assert_called_once()

    def test_declined_cleanup_preserves_other_work(self):
        push = self.git
        for mismatch in ("tip", "tree", "commits", "open_pr", "body", "default"):
            with self.subTest(mismatch=mismatch):
                fork = self.setup_declined_fork()
                if mismatch == "tip":
                    fork.get_git_matching_refs.return_value[0].object.sha = "b" * 40
                elif mismatch == "tree":
                    fork.get_commit.return_value.commit.tree.sha = "changed"
                elif mismatch == "commits":
                    self.pr.commits = 2
                elif mismatch == "open_pr":
                    self.repo.get_pulls.side_effect = [
                        [self.pr, MagicMock(state="open")]
                    ]
                elif mismatch == "body":
                    self.pr.body = "Manual PR"
                else:
                    fork.default_branch = self.change.branch
                main._cleanup_closed_branches(self.repo, fork, "test-token", "master")
                push.assert_not_called()

    def test_existing_pr_delivery(self):
        for state, merged, outcome in (
            ("closed", False, "pr_existing_closed"),
            ("closed", True, "pr_existing_merged"),
            ("open", False, "pr_existing_open"),
        ):
            with self.subTest(outcome=outcome):
                self.pr.state = state
                self.pr.is_merged.return_value = merged
                self.pr.mergeable = False
                report = {}
                self.assertTrue(main.open_pr(self.change, fork=False, report=report))
                self.assertEqual(
                    report["pull_request"],
                    {
                        "url": self.pr.html_url,
                        "outcome": outcome.removeprefix("pr_"),
                    },
                )
                self.pr.merge.assert_not_called()
                self.repo.create_pull.assert_not_called()

    def test_cleanup_continues_after_protected_branch(self):
        fork = self.setup_declined_fork()
        second = MagicMock(ref="refs/heads/update-cc7de96")
        second.object.sha = self.pr.head.sha
        fork.get_git_matching_refs.return_value.append(second)
        other = MagicMock(state="closed", commits=1, body=self.pr.body)
        other.base.ref = "master"
        other.head.repo = fork
        other.head.sha = second.object.sha
        other.head.ref = "update-cc7de96"
        self.repo.get_pulls.side_effect = [[self.pr], [other]]
        fork.get_git_ref.return_value.object.sha = self.pr.head.sha
        self.git.side_effect = [MagicMock(returncode=1), MagicMock(returncode=0)]
        with self.assertLogs("src.main", level="WARNING"):
            main._cleanup_closed_branches(self.repo, fork, "token", "master")
        self.assertEqual(self.git.call_count, 2)
        self.assertEqual(self.git.call_args.args[0][-1], ":refs/heads/update-cc7de96")

    def test_created_pr_delivery(self):
        self.repo.get_pulls.return_value = []
        self.repo.create_pull.return_value = self.pr
        report = {}
        with patch("src.main.subprocess.run") as push:
            push.return_value.returncode = 0
            self.assertTrue(main.open_pr(self.change, fork=False, report=report))
        self.assertEqual(
            report["pull_request"], {"url": self.pr.html_url, "outcome": "created"}
        )
        self.repo.create_pull.assert_called_once()

    def test_cleanup_failure_retains_merge_observation(self):
        self.git.return_value.returncode = 1
        report = {"delivery": "pending"}
        with self.assertRaisesRegex(RuntimeError, "Could not delete"):
            main.open_pr(self.change, fork=False, report=report)
        self.assertEqual(report["pull_request"]["outcome"], "merged")
        self.assertEqual(report["delivery"], "pending")

    def test_comment_failure_does_not_prevent_cleanup(self):
        self.pr.create_issue_comment.side_effect = GithubException(
            403, {"message": "Forbidden"}, {}
        )
        with self.assertLogs("src.main", level="WARNING") as logs:
            self.open_pr()
        self.assertIn("could not post the merge confirmation", logs.output[0])
        self.git.assert_called_once()

    def test_failed_merge_retry_posts_only_one_confirmation(self):
        self.pr.merge.return_value.merged = False
        self.pr.merge.return_value.message = "Merge failed"
        with self.assertRaisesRegex(RuntimeError, "Merge failed"):
            main.open_pr(self.change, fork=False)
        self.pr.create_issue_comment.assert_not_called()

        self.pr.merge.return_value.merged = True
        main.open_pr(self.change, fork=False)
        self.pr.state = "closed"
        self.pr.is_merged.return_value = True
        main.open_pr(self.change, fork=False)

        self.assertEqual(self.pr.merge.call_count, 2)
        self.pr.create_issue_comment.assert_called_once_with(
            main.AUTOMERGE_DUE_TO_CONFIG
        )

    def test_branch_already_missing_at_lookup(self):
        self.repo.get_git_ref.side_effect = UnknownObjectException(
            404, {"message": "Not Found"}, {}
        )
        self.open_pr()
        self.git.assert_not_called()

    def test_branch_deleted_between_lookup_and_delete(self):
        self.git.return_value.returncode = 1
        self.repo.get_git_ref.side_effect = [
            self.ref,
            UnknownObjectException(404, {}, {}),
        ]
        main.open_pr(self.change, fork=False)
        self.git.assert_called_once()

    def test_branch_advanced_after_merge_is_preserved(self):
        self.ref.object.sha = "b" * 40
        self.open_pr()
        self.git.assert_not_called()

    def test_human_commit_before_merge_is_preserved(self):
        self.pr.commits = 2
        main.open_pr(self.change, fork=False)
        self.pr.merge.assert_called_once()
        self.git.assert_not_called()

    def test_amended_commit_with_same_tree_is_preserved(self):
        self.pr.head.sha = self.ref.object.sha = "b" * 40
        main.open_pr(self.change, fork=False)
        self.git.assert_not_called()

    def test_original_attribution_checks_for_force_pushes(self):
        fork = self.setup_declined_fork()
        self.pr.body = (
            "🤖 This pull request was automatically generated by "
            "[flathub/flatpak-external-data-checker](https://github.com/flathub/flatpak-external-data-checker)."
        )
        for count in (1, 0):
            self.git.reset_mock()
            self.pr.requester.graphql_node.return_value = (
                {},
                {"data": {"node": {"timelineItems": {"totalCount": count}}}},
            )
            main._cleanup_closed_branches(self.repo, fork, "token", "master")
            if count:
                self.git.assert_not_called()
            else:
                self.git.assert_called_once()

    def test_decline_on_renamed_fork_survives_checkout_and_permission_change(self):
        fork = self.setup_declined_fork()
        fork.full_name = "update-bot/renamed"
        self.repo.permissions.push = True
        self.github.return_value.get_user.return_value.login = "update-bot"
        self.repo.get_forks.return_value = [fork]
        self.pr.head.ref = "update-cc7de96"
        self.repo.get_pulls.side_effect = lambda *, state, base, head: (
            [self.pr]
            if (
                state == "closed"
                and base == "master"
                and head == "update-bot:update-cc7de96"
            )
            else []
        )
        report = {}
        main.open_pr(self.change, report=report)
        self.assertEqual(report["pull_request"]["outcome"], "existing_closed")
        self.repo.create_pull.assert_not_called()

    def test_unrelated_cleanup_errors_propagate(self):
        for error in (
            GithubException(401, {"message": "Bad credentials"}, {}),
            GithubException(403, {"message": "Forbidden"}, {}),
            GithubException(422, {"message": "Validation failed"}, {}),
            GithubException(500, {"message": "Internal server error"}, {}),
            ConnectionError("Connection lost"),
        ):
            with self.subTest(error=error):
                self.repo.get_git_ref.side_effect = error
                with self.assertRaises(type(error)) as raised:
                    main.open_pr(self.change, fork=False)
                self.assertIs(raised.exception, error)

    def test_merge_errors_propagate_without_cleanup(self):
        for status in (404, 405, 500):
            with self.subTest(status=status):
                error = GithubException(status, {"message": "Merge failed"}, {})
                self.pr.merge.side_effect = error
                with self.assertRaises(GithubException) as raised:
                    main.open_pr(self.change, fork=False)
                self.assertIs(raised.exception, error)
                self.repo.get_git_ref.assert_not_called()
                self.pr.create_issue_comment.assert_not_called()

    def test_unsuccessful_merge_response_does_not_clean_up(self):
        self.pr.merge.return_value.merged = False
        self.pr.merge.return_value.message = "Merge failed"
        with self.assertRaisesRegex(RuntimeError, "Merge failed"):
            main.open_pr(self.change, fork=False)
        self.repo.get_git_ref.assert_not_called()
        self.pr.create_issue_comment.assert_not_called()
