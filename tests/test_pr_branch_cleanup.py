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
        self.repo = self.github.return_value.get_repo.return_value
        self.repo.default_branch = "master"
        self.repo.owner.login = "flathub"
        self.pr = MagicMock()
        self.pr.state = "open"
        self.pr.head.ref = "update-master-cc7de96"
        self.pr.mergeable = True
        commit = self.pr.head.repo.get_commit.return_value
        commit.get_combined_status.return_value.state = "success"
        self.pr.merge.return_value.merged = True
        self.repo.get_pulls.return_value = [self.pr]
        self.ref = self.repo.get_git_ref.return_value
        self.change = main.CommittedChanges(
            subject="Update Pods",
            body="",
            commit="cc7de96",
            branch=self.pr.head.ref,
            base_branch="master",
        )

    def open_pr(self):
        main.open_pr(self.change, fork=False)
        self.pr.merge.assert_called_once_with(merge_method="rebase")
        self.repo.get_git_ref.assert_called_once_with("heads/update-master-cc7de96")
        self.repo.create_pull.assert_not_called()
        self.push.assert_not_called()

    def test_successful_cleanup(self):
        self.open_pr()
        self.ref.delete.assert_called_once_with()
        self.pr.assert_has_calls(
            [
                call.merge(merge_method="rebase"),
                call.create_issue_comment(main.AUTOMERGE_DUE_TO_CONFIG),
            ]
        )

    def test_comment_failure_does_not_prevent_cleanup(self):
        self.pr.create_issue_comment.side_effect = GithubException(
            403, {"message": "Forbidden"}, {}
        )
        with self.assertLogs("src.main", level="WARNING") as logs:
            self.open_pr()
        self.assertIn("could not post the merge confirmation", logs.output[0])
        self.ref.delete.assert_called_once_with()

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
        self.ref.delete.assert_not_called()

    def test_branch_deleted_between_lookup_and_delete(self):
        self.ref.delete.side_effect = GithubException(
            404, {"message": "Reference does not exist"}, {}
        )
        self.open_pr()
        self.ref.delete.assert_called_once_with()

    def test_unrelated_cleanup_errors_propagate(self):
        for operation in (self.repo.get_git_ref, self.ref.delete):
            for error in (
                GithubException(401, {"message": "Bad credentials"}, {}),
                GithubException(403, {"message": "Forbidden"}, {}),
                GithubException(422, {"message": "Validation failed"}, {}),
                GithubException(500, {"message": "Internal server error"}, {}),
                ConnectionError("Connection lost"),
            ):
                with self.subTest(operation=operation, error=error):
                    operation.side_effect = error
                    with self.assertRaises(type(error)) as raised:
                        main.open_pr(self.change, fork=False)
                    self.assertIs(raised.exception, error)
                    operation.side_effect = None

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
