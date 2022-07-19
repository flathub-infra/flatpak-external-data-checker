#!/usr/bin/env python3
#
# flatpak-extra-data-checker: A tool for checking the status of
# the extra data in a Flatpak manifest.
#
# Copyright (C) 2018 Endless Mobile, Inc.
#
# Authors:
#       Joaquim Rocha <jrocha@endlessm.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import argparse
import contextlib
import json
import logging
import os
import subprocess
import sys
import asyncio
from enum import IntFlag
import typing as t

from github import Github

from .lib.utils import parse_github_url, init_logging
from .lib.externaldata import ExternalData
from . import manifest


log = logging.getLogger(__name__)


@contextlib.contextmanager
def indir(path):
    """
    >>> with indir(path):
    ...    # code executes with 'path' as working directory
    ... # old working directory is restored
    """

    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def print_outdated_external_data(manifest_checker: manifest.ManifestChecker):
    ext_data = manifest_checker.get_outdated_external_data()
    for data in ext_data:
        state_txt = data.state.name or str(data.state)
        message_tmpl = ""
        message_args = {}
        if data.new_version:
            if data.type == data.Type.GIT:
                assert data.new_version
                message_tmpl = (
                    "{data_state}: {data_name}\n"
                    " Has a new version:\n"
                    "  URL:       {url}\n"
                    "  Commit:    {commit}\n"
                    "  Tag:       {tag}\n"
                    "  Branch:    {branch}\n"
                    "  Version:   {version}\n"
                    "  Timestamp: {timestamp}\n"
                )
                message_args = data.new_version._asdict()
            else:
                assert isinstance(data, ExternalData)
                assert data.new_version
                message_tmpl = (
                    "{data_state}: {data_name}\n"
                    " Has a new version:\n"
                    "  URL:       {url}\n"
                    "  MD5:       {md5}\n"
                    "  SHA1:      {sha1}\n"
                    "  SHA256:    {sha256}\n"
                    "  SHA512:    {sha512}\n"
                    "  Size:      {size}\n"
                    "  Version:   {version}\n"
                    "  Timestamp: {timestamp}\n"
                )
                message_args = {
                    **data.new_version._asdict(),
                    **data.new_version.checksum._asdict(),
                }
        elif data.State.BROKEN in data.state:
            message_tmpl = (
                # fmt: off
                "{data_state}: {data_name}\n"
                " Couldn't get new version for {url}\n"
                # fmt: on
            )
            message_args = data.current_version._asdict()

        message = message_tmpl.format(
            data_state=state_txt,
            data_name=data.filename,
            **message_args,
        )
        print(message, flush=True)
    return len(ext_data)


def print_errors(manifest_checker: manifest.ManifestChecker) -> int:
    # TODO: Actually do pretty-print collected errors
    errors = manifest_checker.get_errors()
    return len(errors)


def check_call(args):
    log.debug("$ %s", " ".join(args))
    subprocess.check_call(args)


class CommittedChanges(t.NamedTuple):
    subject: str
    body: t.Optional[str]
    commit: str
    branch: str
    base_branch: t.Optional[str]


def commit_changes(changes: t.List[str]) -> CommittedChanges:
    log.info("Committing updates")
    body: t.Optional[str]
    if len(changes) > 1:
        subject = "Update {} modules".format(len(changes))
        body = "\n".join(changes)
        message = subject + "\n\n" + body
    else:
        subject = changes[0]
        body = None
        message = subject

    # Remember the base branch
    base_branch: t.Optional[str]
    base_branch = subprocess.check_output(
        ["git", "branch", "--show-current"], text=True
    ).strip()
    if not base_branch:
        base_branch = None

    # Moved to detached HEAD
    check_call(["git", "checkout", "HEAD@{0}"])
    check_call(["git", "commit", "-am", message])

    # Find a stable identifier for the contents of the tree, to avoid
    # sending the same PR twice.
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    branch = f"update-{tree[:7]}"

    try:
        # Check if the branch already exists
        subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            capture_output=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        # If not, create it
        check_call(["git", "checkout", "-b", branch])
    return CommittedChanges(
        subject=subject,
        body=body,
        commit=tree,
        branch=branch,
        base_branch=base_branch,
    )


DISCLAIMER = (
    "🤖 This pull request was automatically generated by "
    "[flathub/flatpak-external-data-checker]"
    "(https://github.com/flathub/flatpak-external-data-checker). "
    "Please [open an issue]"
    "(https://github.com/flathub/flatpak-external-data-checker/issues/new) "
    "if you have any questions or complaints. 🤖"
)

AUTOMERGE_DUE_TO_CONFIG = (
    "🤖 This PR passed CI, and `automerge-flathubbot-prs` is `true` in "
    "`flathub.json`, so I'm merging it automatically. 🤖"
)

AUTOMERGE_DUE_TO_BROKEN_URLS = (
    "🤖 The currently-published version contains broken URLs, and this PR passed "
    "CI, so I'm merging it automatically. You can disable this behaviour by setting "
    "`automerge-flathubbot-prs` to `false` in flathub.json. 🤖"
)


def open_pr(
    change: CommittedChanges,
    manifest_checker: manifest.ManifestChecker = None,
    fork: t.Optional[bool] = None,
):
    try:
        github_token = os.environ["GITHUB_TOKEN"]
    except KeyError:
        log.error("GITHUB_TOKEN environment variable is not set")
        sys.exit(1)

    log.info("Opening pull request for branch %s", change.branch)
    g = Github(github_token)
    user = g.get_user()

    origin_url = (
        subprocess.check_output(["git", "remote", "get-url", "origin"])
        .decode("utf-8")
        .strip()
    )
    origin_repo = g.get_repo(parse_github_url(origin_url))

    if fork is True:
        log.debug("creating fork (as requested)")
        repo = user.create_fork(origin_repo)
    elif fork is False:
        log.debug("not creating fork (as requested)")
        repo = origin_repo
    elif origin_repo.permissions.push:
        log.debug("origin repo is writable; not creating fork")
        repo = origin_repo
    else:
        log.debug("origin repo not writable; creating fork")
        repo = user.create_fork(origin_repo)

    remote_url = f"https://{github_token}:x-oauth-basic@github.com/{repo.full_name}"

    base = change.base_branch or origin_repo.default_branch

    head = "{}:{}".format(repo.owner.login, change.branch)
    pr_message = ((change.body or "") + "\n\n" + DISCLAIMER).strip()

    try:
        with open("flathub.json") as f:
            repocfg = json.load(f)
    except FileNotFoundError:
        repocfg = {}

    automerge = repocfg.get("automerge-flathubbot-prs")
    # Implicitly automerge if…
    force_automerge = (
        # …the user has not explicitly disabled automerge…
        automerge is not False
        # …and we have a manifest checker (i.e. we're not in a test)…
        and manifest_checker
        # …and at least one source is broken and has an update
        and any(
            data.type == data.Type.EXTRA_DATA
            and data.State.BROKEN in data.state
            and data.new_version
            for data in manifest_checker.get_outdated_external_data()
        )
    )

    prs = origin_repo.get_pulls(state="all", base=base, head=head)

    # If the maintainer has closed our last PR or it was merged,
    # we don't want to open another one.
    closed_prs = [pr for pr in prs if pr.state == "closed"]
    for pr in closed_prs:
        log.info(
            "Found existing %s PR: %s",
            "merged" if pr.is_merged() else pr.state,
            pr.html_url,
        )
        return

    open_prs = [pr for pr in prs if pr.state == "open"]
    for pr in open_prs:
        log.info("Found open PR: %s", pr.html_url)

        if automerge or force_automerge:
            pr_commit = pr.head.repo.get_commit(pr.head.sha)
            if pr_commit.get_combined_status().state == "success" and pr.mergeable:
                log.info("PR passed CI and is mergeable, merging %s", pr.html_url)
                if automerge:
                    pr.create_issue_comment(AUTOMERGE_DUE_TO_CONFIG)
                else:  # force_automerge
                    pr.create_issue_comment(AUTOMERGE_DUE_TO_BROKEN_URLS)
                pr.merge(merge_method="rebase")
                origin_repo.get_git_ref(f"heads/{pr.head.ref}").delete()

        return

    check_call(["git", "push", "-u", remote_url, change.branch])

    log.info(
        "Creating pull request in %s from head `%s` to base `%s`",
        origin_repo.html_url,
        head,
        base,
    )
    pr = origin_repo.create_pull(
        change.subject,
        pr_message,
        base,
        head,
        maintainer_can_modify=True,
    )
    log.info("Opened pull request %s", pr.html_url)


def parse_cli_args(cli_args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest", help="Flatpak manifest to check", type=os.path.abspath
    )
    parser.add_argument(
        "-v", "--verbose", help="Print debug messages", action="store_true"
    )
    parser.add_argument(
        "--update",
        help="Update manifest(s) to refer to new versions of "
        "external data - also open PRs for changes unless "
        "--commit-only is specified",
        action="store_true",
    )
    parser.add_argument(
        "--commit-only",
        help="Do not open PRs for updates, only commit changes "
        "to external data (implies --update)",
        action="store_true",
    )
    parser.add_argument(
        "--edit-only",
        help="Do not commit changes, only update files (implies --update)",
        action="store_true",
    )
    parser.add_argument(
        "--check-outdated",
        help="Exit with non-zero status if outdated sources were found and not updated",
        action="store_true",
    )
    parser.add_argument(
        "--filter-type",
        help="Only check external data of the given type",
        type=ExternalData.Type,
        choices=list(ExternalData.Type),
    )

    fork = parser.add_argument_group(
        "control forking behaviour",
        "By default, %(prog)s pushes directly to the GitHub repo if the GitHub "
        "token has permission to do so, and creates a fork if not.",
    ).add_mutually_exclusive_group()
    fork.add_argument(
        "--always-fork",
        action="store_const",
        const=True,
        dest="fork",
        help=(
            "Always push to a fork, even if the user has write access to the "
            "upstream repo"
        ),
    )
    fork.add_argument(
        "--never-fork",
        action="store_const",
        const=False,
        dest="fork",
        help=(
            "Never push to a fork, even if this means failing to push to the "
            "upstream repo"
        ),
    )
    parser.add_argument(
        "--unsafe",
        help="Enable unsafe features; use only with manifests from trusted sources",
        action="store_true",
    )
    parser.add_argument(
        "--max-manifest-size",
        help="Maximum manifest file size allowed to load",
        type=int,
        default=manifest.CheckerOptions.max_manifest_size,
    )
    parser.add_argument(
        "--require-important-update",
        help="Require an update to at least one source with is-important or is-main-source to save changes to the manifest. "
        "If no instances of is-important or is-main-source are found, assume normal behaviour and always save changes to the manifest. "
        "This is useful to avoid PRs generated to update a singular unimportant source.",
        action="store_true",
    )

    return parser.parse_args(cli_args)


async def run_with_args(args: argparse.Namespace) -> t.Tuple[int, int, bool]:
    init_logging(logging.DEBUG if args.verbose else logging.INFO)

    should_update = args.update or args.commit_only or args.edit_only
    did_update = False

    options = manifest.CheckerOptions(
        allow_unsafe=args.unsafe,
        max_manifest_size=args.max_manifest_size,
        require_important_update=args.require_important_update,
    )

    manifest_checker = manifest.ManifestChecker(args.manifest, options)

    await manifest_checker.check(args.filter_type)

    outdated_num = print_outdated_external_data(manifest_checker)

    if should_update and outdated_num > 0:
        changes = manifest_checker.update_manifests()
        if changes and not args.edit_only:
            with indir(os.path.dirname(args.manifest)):
                committed_changes = commit_changes(changes)
                if not args.commit_only:
                    open_pr(
                        committed_changes,
                        manifest_checker=manifest_checker,
                        fork=args.fork,
                    )
        did_update = True

    errors_num = print_errors(manifest_checker)

    log.log(
        logging.WARNING if errors_num else logging.INFO,
        "Check finished with %i error(s)",
        errors_num,
    )

    return outdated_num, errors_num, did_update


class ResultCode(IntFlag):
    SUCCESS = 0
    ERROR = 1
    OUTDATED = 2


def main():
    res = ResultCode.SUCCESS
    args = parse_cli_args()
    outdated_num, errors_num, updated = asyncio.run(run_with_args(args))
    if errors_num:
        res |= ResultCode.ERROR
    if args.check_outdated and not updated and outdated_num > 0:
        res |= ResultCode.OUTDATED
    sys.exit(res)
