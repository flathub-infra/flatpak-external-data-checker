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
import asyncio
import contextlib
import datetime
import getpass
import json
import logging
import os
import subprocess
import sys
import tempfile
import typing as t
from enum import IntFlag
from pathlib import Path

from github import Github, GithubException

from . import manifest
from .lib.errors import ManifestLoadError
from .lib.externaldata import ExternalData
from .lib.provenance import checker_provenance
from .lib.runtime import RuntimeEolChecker
from .lib.sourceupdates import format_source_updates
from .lib.utils import init_logging, parse_github_url, read_manifest
from .lint import ManifestLinter

log = logging.getLogger(__name__)


@contextlib.contextmanager
def indir(path: Path):
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


def print_outdated_external_data(manifest_checker: manifest.ManifestChecker) -> int:
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
                "{data_state}: {data_name}\n Couldn't get new version for {url}\n"
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
    errors = manifest_checker.get_errors()
    details = "".join(f"\n  {i}. {error}" for i, error in enumerate(errors, start=1))
    log.log(
        logging.WARNING if errors else logging.INFO,
        "Check finished with %i error(s)%s",
        len(errors),
        details,
    )
    return len(errors)


def check_call(args):
    log.debug("$ %s", " ".join(args))
    subprocess.check_call(args)


def get_manifest_git_checkout(manifest: Path | str) -> Path:
    # Can't use git rev-parse --show-toplevel because of a chicken-and-egg problem: we
    # need to find the checkout directory so that we can mark it as safe so that we can
    # use git against it.
    for directory in Path(manifest).parents:
        if os.path.exists(directory / ".git"):
            return directory

    raise FileNotFoundError(f"Cannot find git checkout for {manifest}")


def is_end_of_life(manifest_path: Path | str) -> bool:
    path = Path(manifest_path).resolve()
    try:
        directory = get_manifest_git_checkout(path)
    except FileNotFoundError:
        directory = path.parent
    config_path = directory / "flathub.json"
    if not config_path.exists():
        return False
    config = json.loads(config_path.read_text())
    if not isinstance(config, dict):
        raise ValueError("flathub.json must be an object")
    return any(
        config.get(key) is not None and config.get(key) is not False
        for key in ("end-of-life", "end-of-life-rebase")
    )


def ensure_git_safe_directory(checkout: Path):
    uid = os.getuid()
    checkout_uid = os.stat(checkout).st_uid
    if uid == checkout_uid:
        return

    try:
        result = subprocess.run(
            ["git", "config", "--get-all", "safe.directory"],
            check=True,
            capture_output=True,
            encoding="utf-8",
        )
        safe_dirs = [Path(x) for x in result.stdout.splitlines()]
    except subprocess.CalledProcessError as err:
        # git config --get-all will return 1 if the key doesn't exist.
        # Re-raise the error for anything else.
        if err.returncode != 1:
            raise
        safe_dirs = []

    if checkout in safe_dirs:
        return

    log.info("Adding %s git safe directory", checkout)
    location = "--system" if uid == 0 else "--global"
    check_call(["git", "config", location, "--add", "safe.directory", str(checkout)])


class CommittedChanges(t.NamedTuple):
    subject: str
    body: str | None
    commit: str | None
    branch: str
    base_branch: str | None


def commit_message(changes: list[str]) -> str:
    assert len(changes) >= 1

    if len(changes) == 1:
        return changes[0]

    module_names = list(dict.fromkeys(list(i.split(":", 1)[0] for i in changes)))

    if len(module_names) == 1:
        return f"Update {module_names[0]} module"

    for i in reversed(range(2, len(module_names) + 1)):
        xs = module_names[: i - 1]
        y = module_names[i - 1]
        zs = module_names[i:]

        if zs:
            tail = f" and {len(zs)} more modules"
            xs.append(y)
        else:
            tail = f" and {y} modules"

        subject = "Update " + ", ".join(xs) + tail
        if len(subject) <= 70:
            return subject

    return f"Update {len(module_names)} modules"


def branch_exists(branch: str) -> bool:
    try:
        subprocess.run(
            ["git", "rev-parse", "--verify", branch],
            capture_output=True,
            check=True,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def commit_changes(
    changes: list[str], commit_to_base: bool = False, base_branch: str | None = None
) -> CommittedChanges | None:
    log.info("Committing updates")
    body: str | None
    subject = commit_message(changes)
    if len(changes) > 1:
        body = "\n".join(changes)
        message = subject + "\n\n" + body
    else:
        body = None
        message = subject

    # Remember the base branch
    if base_branch is None:
        base_branch = subprocess.check_output(
            ["git", "branch", "--show-current"], text=True
        ).strip()
    if not base_branch:
        base_branch = None

    if commit_to_base:
        if not base_branch:
            log.error("Failed to determine the base branch: cannot commit to it")
            return None
        branch = base_branch
    else:
        # Moved to detached HEAD
        log.info("Switching to detached HEAD")
        check_call(["git", "-c", "advice.detachedHead=false", "checkout", "HEAD@{0}"])

    retry_commit = False
    try:
        check_call(["git", "commit", "-am", message])
    except subprocess.CalledProcessError:
        retry_commit = True

    if retry_commit:
        log.warning("Committing failed. Falling back to a sanitised config")
        git_name = getpass.getuser()
        git_email = git_name + "@" + "localhost"
        assert git_email is not None
        env = {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG": "''",
        }
        subprocess.run(
            [
                "git",
                "-c",
                f"user.name={git_name}",
                "-c",
                f"user.email={git_email}",
                "commit",
                "--no-verify",
                "--no-gpg-sign",
                "-am",
                message,
            ],
            check=True,
            env=env,
        )

    # Find a stable identifier for the contents of the tree, to avoid
    # sending the same PR twice.
    tree = subprocess.check_output(
        ["git", "rev-parse", "HEAD^{tree}"], text=True
    ).strip()

    if not commit_to_base:
        branch = (
            f"update-{base_branch}-{tree[:7]}" if base_branch else f"update-{tree[:7]}"
        )

    if not (commit_to_base or branch_exists(branch)):
        check_call(["git", "checkout", "-b", branch])
    return CommittedChanges(
        subject=subject,
        body=body,
        commit=tree,
        branch=branch,
        base_branch=base_branch,
    )


DISCLAIMER = (
    "🤖 Created by "
    "[Flatpak External Data Checker]"
    "(https://github.com/flathub-infra/flatpak-external-data-checker). "
    "[Report a problem with the checker]"
    "(https://github.com/flathub-infra/flatpak-external-data-checker/issues/new)."
)

AUTOMERGE_DUE_TO_CONFIG = (
    "### 🤖 Merged automatically\n\n"
    "- **Reason:** `automerge-flathubbot-prs` is `true` in `flathub.json`.\n"
    "- **Checks:** The combined commit status was successful. GitHub reported "
    "this PR as mergeable.\n\n"
    "To disable automatic merging for future PRs, set `automerge-flathubbot-prs` "
    "to `false` in `flathub.json`."
)

AUTOMERGE_DUE_TO_BROKEN_URLS = (
    "### 🤖 Merged automatically\n\n"
    "- **Reason:** This PR updated a broken extra-data source.\n"
    "- **Checks:** The combined commit status was successful. GitHub reported "
    "this PR as mergeable.\n\n"
    "To disable automatic merging for future PRs, set `automerge-flathubbot-prs` "
    "to `false` in `flathub.json`. This also applies to broken extra-data sources."
)


def open_pr(
    change: CommittedChanges,
    manifest_checker: manifest.ManifestChecker | None = None,
    fork: bool | None = None,
    pr_labels: list[str] | None = None,
    report: dict | None = None,
) -> bool:

    try:
        github_token = os.environ["GITHUB_TOKEN"]
    except KeyError:
        log.error("GITHUB_TOKEN environment variable is not set")
        sys.exit(1)

    log.info("Opening pull request for branch %s", change.branch)
    g = Github(github_token)

    origin_url = (
        subprocess.check_output(["git", "remote", "get-url", "origin"])
        .decode("utf-8")
        .strip()
    )
    origin_repo = g.get_repo(parse_github_url(origin_url))

    if fork is True:
        log.debug("creating fork (as requested)")
        repo = origin_repo.create_fork()
    elif fork is False:
        log.debug("not creating fork (as requested)")
        repo = origin_repo
    elif origin_repo.permissions.push:
        log.debug("origin repo is writable; not creating fork")
        repo = origin_repo
    else:
        log.debug("origin repo not writable; creating fork")
        repo = origin_repo.create_fork()

    remote_url = f"https://x-access-token:{github_token}@github.com/{repo.full_name}"

    base = change.base_branch or origin_repo.default_branch

    head = f"{repo.owner.login}:{change.branch}"
    source_details = (
        format_source_updates(manifest_checker.source_updates)
        if manifest_checker is not None
        else ""
    )
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
            for data in manifest_checker.get_outdated_external_data(
                include_blocked=False
            )
        )
    )

    configuration = ""
    merge_conditions = (
        "- **When:** The checker can merge this PR on a later run.\n"
        "- **Requirements:** The combined commit status must be successful and "
        "GitHub must report the PR as mergeable. "
        "Repository rules and token permissions must also allow the merge."
    )
    if automerge is True:
        configuration = (
            "### Configuration\n\n"
            "**Automerge is enabled.**\n\n"
            "- **Setting:** `automerge-flathubbot-prs` is `true` in `flathub.json`.\n"
            f"{merge_conditions}"
        )
    elif automerge is False:
        configuration = (
            "### Configuration\n\n"
            "**Automerge is disabled.**\n\n"
            "- **Setting:** `automerge-flathubbot-prs` is `false` in `flathub.json`. "
            "This also applies to broken extra-data sources.\n\n"
            "Merge this PR manually when it is ready."
        )
    elif force_automerge:
        configuration = (
            "### Configuration\n\n"
            "**Automerge is enabled for this broken-source update.**\n\n"
            "- **Reason:** This PR updates a broken extra-data source. `flathub.json` "
            "does not disable automatic merging.\n"
            f"{merge_conditions}\n\n"
            "To disable automatic merging, set `automerge-flathubbot-prs` "
            "to `false` in `flathub.json`."
        )

    build_guidance = ""
    if origin_repo.full_name.split("/")[0].lower() == "flathub":
        build_guidance = (
            "### Test build\n\n"
            "Comment `bot, build` on this PR to "
            "[start another Flathub test build]"
            "(https://docs.flathub.org/docs/for-app-authors/maintenance#test-builds).\n\n"
            "This does not rerun the external data checker or rebase the PR."
        )
    pr_message = "\n\n".join(
        part
        for part in (
            source_details
            or f"### Updates\n\n{change.body or change.subject or 'Source update'}",
            configuration,
            build_guidance,
        )
        if part
    )
    pr_message = "\n\n---\n\n".join(part for part in (pr_message, DISCLAIMER) if part)

    prs = origin_repo.get_pulls(state="all", base=base, head=head)

    # If the maintainer has closed our last PR or it was merged,
    # we don't want to open another one.
    closed_prs = [pr for pr in prs if pr.state == "closed"]
    for pr in closed_prs:
        merged = pr.is_merged()
        if report is not None:
            report["pull_request"] = {
                "url": pr.html_url,
                "outcome": "existing_merged" if merged else "existing_closed",
            }
        log.info(
            "Found existing %s PR: %s",
            "merged" if merged else pr.state,
            pr.html_url,
        )
        return True

    open_prs = [pr for pr in prs if pr.state == "open"]
    for pr in open_prs:
        if report is not None:
            report["pull_request"] = {
                "url": pr.html_url,
                "outcome": "existing_open",
            }
        log.info("Found open PR: %s", pr.html_url)

        if automerge or force_automerge:
            pr_commit = pr.head.repo.get_commit(pr.head.sha)
            if pr_commit.get_combined_status().state == "success" and pr.mergeable:
                log.info("PR passed CI and is mergeable, merging %s", pr.html_url)
                result = pr.merge(merge_method="rebase")
                if not result.merged:
                    raise RuntimeError(
                        f"Failed to merge {pr.html_url}: {result.message}"
                    )
                if report is not None:
                    report["pull_request"]["outcome"] = "merged"
                try:
                    pr.create_issue_comment(
                        AUTOMERGE_DUE_TO_CONFIG
                        if automerge
                        else AUTOMERGE_DUE_TO_BROKEN_URLS
                    )
                except GithubException:
                    log.warning(
                        "Merged %s, but could not post the merge confirmation",
                        pr.html_url,
                        exc_info=True,
                    )
                try:
                    origin_repo.get_git_ref(f"heads/{pr.head.ref}").delete()
                except GithubException as error:
                    if error.status != 404:
                        raise
                    log.info("Merged PR branch %s is already deleted", pr.head.ref)

        return True

    # An empty lease requires the remote ref to be absent, even if another
    # writer creates it after the PR lookup.
    ref = f"refs/heads/{change.branch}"
    push_result = subprocess.run(
        [
            "git",
            "push",
            "--porcelain",
            f"--force-with-lease={ref}:",
            remote_url,
            f"HEAD:{ref}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if push_result.returncode:
        details = (push_result.stdout + push_result.stderr).replace(
            github_token, "<REDACTED>"
        )
        log.error(
            "Failed to publish update branch %s in %s (git exit %s). "
            "If the branch already exists without a matching PR, inspect it "
            "before retrying; existing remote work will not be overwritten.\n%s",
            change.branch,
            repo.full_name,
            push_result.returncode,
            details.strip(),
        )
        return False

    log.info(
        "Creating pull request in %s from head `%s` to base `%s`",
        origin_repo.html_url,
        head,
        base,
    )

    gh_run_id = os.environ.get("GITHUB_RUN_ID")
    gh_repo_name = os.environ.get("GITHUB_REPOSITORY")
    if gh_run_id and gh_repo_name:
        log.info("Appending GitHub actions log URL to PR message")
        log_url = f"https://github.com/{gh_repo_name}/actions/runs/{gh_run_id}"
        pr_message += f"\n\n[View checker logs]({log_url})"

    subject = change.subject or ""

    if base != origin_repo.default_branch:
        subject = f"[{base}] {subject}"

    pr = origin_repo.create_pull(
        title=subject,
        body=pr_message,
        base=base,
        head=head,
        maintainer_can_modify=True,
    )
    log.info("Opened pull request %s", pr.html_url)
    if report is not None:
        report["pull_request"] = {"url": pr.html_url, "outcome": "created"}
    if pr_labels:
        log.info("Adding labels to PR: %s", ", ".join(pr_labels))
        pr.set_labels(*pr_labels)
    return True


def parse_cli_args(cli_args=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest", help="Flatpak manifest to check", type=os.path.abspath
    )
    parser.add_argument(
        "-v", "--verbose", help="Print debug messages", action="store_true"
    )
    parser.add_argument(
        "--lint",
        help="Validate manifest and checker metadata offline without changing files",
        action="store_true",
    )
    parser.add_argument(
        "--report-json",
        metavar="FILE",
        type=os.path.abspath,
        help="Write a versioned JSON health report to FILE (including failed runs)",
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
        default=manifest.MAX_MANIFEST_SIZE,
    )
    parser.add_argument(
        "--require-important-update",
        help=(
            "Require an update to at least one source with is-important or "
            "is-main-source to save changes to the manifest. If no instances of "
            "is-important or is-main-source are found, assume normal behaviour and "
            "always save changes to the manifest. This is useful to avoid PRs "
            "generated to update a singular unimportant source."
        ),
        action="store_true",
    )
    parser.add_argument(
        "--pr-labels",
        type=str,
        default="",
        help="Comma-separated GitHub labels to add to the pull request",
    )
    parser.add_argument(
        "--commit-to-current-branch",
        action="store_true",
        help=(
            "Commit changes directly to the currently checked out branch"
            " instead of creating a new branch"
        ),
    )
    parser.add_argument(
        "--enable-robots-txt",
        action="store_true",
        help="Enable respecting robots.txt rules (default: False)",
    )

    args = parser.parse_args(cli_args)
    if args.lint and (
        args.update
        or args.commit_only
        or args.edit_only
        or args.check_outdated
        or args.filter_type
        or args.commit_to_current_branch
    ):
        parser.error(
            "--lint cannot be combined with update or source-filtering options"
        )
    args.pr_labels = [
        label.strip() for label in args.pr_labels.split(",") if label.strip()
    ]

    return args


def get_checkout_identity(manifest_path: str) -> dict:
    identity = {"commit": None, "branch": None, "dirty": None}
    try:
        checkout = get_manifest_git_checkout(manifest_path)
        for field, command in (
            ("commit", ["rev-parse", "--verify", "HEAD"]),
            ("branch", ["symbolic-ref", "--quiet", "HEAD"]),
            (
                "dirty",
                [
                    "status",
                    "--porcelain",
                    "--untracked-files=normal",
                    "--ignore-submodules=all",
                ],
            ),
        ):
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={checkout}",
                    "-C",
                    str(checkout),
                    *command,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                if field == "branch":
                    identity[field] = result.stdout.strip().removeprefix("refs/heads/")
                else:
                    identity[field] = (
                        bool(result.stdout)
                        if field == "dirty"
                        else result.stdout.strip()
                    )
    except (OSError, UnicodeError):
        pass
    return identity


async def run_with_args(args: argparse.Namespace) -> tuple[int, int, bool]:
    report: dict = {
        "schema_version": 1,
        "manifest": args.manifest,
        "checkout": get_checkout_identity(args.manifest),
        "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": "lint" if args.lint else "check",
        "status": "failed",
        "delivery": "not_requested",
        "sources": [],
        "errors": [],
    }
    try:
        if getattr(args, "report_json", None):
            report["checker"] = checker_provenance()
        result = await _run_with_args(args, report)
        if (
            result[1]
            or result[0] == -1
            or report["errors"]
            or any(
                branch["status"] == "failed" for branch in report.get("branches", [])
            )
        ):
            report["status"] = "failed"
        elif report["status"] != "skipped":
            report["status"] = "completed"
        return result
    except Exception as error:
        report["errors"].append({"type": type(error).__name__, "message": str(error)})
        if report["delivery"] == "pending":
            report["delivery"] = "failed"
        raise
    finally:
        if getattr(args, "report_json", None):
            report["finished_at"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            destination = Path(args.report_json)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=destination.parent,
                    delete=False,
                ) as output:
                    temporary = Path(output.name)
                    json.dump(report, output, indent=2)
                    output.write("\n")
                os.replace(temporary, destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)


async def _run_with_args(
    args: argparse.Namespace, report: dict
) -> tuple[int, int, bool]:
    init_logging(logging.DEBUG if args.verbose else logging.INFO)

    return await run_manifest_with_args(args, report)


def is_extension_manifest(path: str, max_manifest_size: int) -> bool:
    try:
        if Path(path).stat().st_size > max_manifest_size:
            return False
        contents = read_manifest(path)
    except Exception:
        # Full loading reports errors after the EOL check for ordinary manifests.
        return False
    if isinstance(contents, tuple):
        contents = contents[0]
    return (
        isinstance(contents, dict)
        and ("id" in contents or "app-id" in contents)
        and contents.get("build-extension") is True
    )


def get_runtime_branch_manifest(path: Path) -> Path:
    if path.exists():
        return path
    candidates = [
        candidate
        for suffix in (".json", ".yaml", ".yml")
        if (candidate := path.with_suffix(suffix)).is_file()
    ]
    if not candidates:
        raise FileNotFoundError(f"Cannot find runtime branch manifest for {path}")
    if len(candidates) > 1:
        raise ValueError(
            f"Ambiguous runtime branch manifest for {path}: "
            + ", ".join(str(candidate) for candidate in candidates)
        )
    return candidates[0]


async def run_runtime_branches(
    args: argparse.Namespace,
    report: dict,
) -> tuple[int, int, bool] | None:
    try:
        checkout = get_manifest_git_checkout(args.manifest)
    except FileNotFoundError:
        return None
    try:
        ensure_git_safe_directory(checkout)
        manifest_path = Path(args.manifest).relative_to(checkout)
        with indir(checkout):
            origin = subprocess.run(
                ["git", "remote", "get-url", "origin"], capture_output=True, check=False
            )
            if origin.returncode:
                return None
            check_call(
                [
                    "git",
                    "fetch",
                    "--prune",
                    "origin",
                    "+refs/heads/branch/*:refs/remotes/origin/branch/*",
                ]
            )
            refs = subprocess.check_output(
                [
                    "git",
                    "for-each-ref",
                    "--format=%(refname)",
                    "refs/remotes/origin/branch/",
                ],
                text=True,
            ).splitlines()
            if not refs:
                return None

            outdated_total = errors_total = 0
            all_updated = True
            any_updated = False
            runtime_eol = RuntimeEolChecker()
            for ref in refs:
                branch = ref.removeprefix("refs/remotes/origin/")
                log.info("Checking runtime branch %s", branch)
                branch_report: dict = {
                    "branch": branch,
                    "checkout": {"commit": None, "branch": branch, "dirty": None},
                    "status": "failed",
                    "delivery": "not_requested",
                    "sources": [],
                    "errors": [],
                }
                outdated = errors = 0
                updated = False
                try:
                    with tempfile.TemporaryDirectory(prefix="fedc-") as directory:
                        worktree = Path(directory) / "checkout"
                        check_call(
                            ["git", "worktree", "add", "--detach", str(worktree), ref]
                        )
                        try:
                            branch_args = argparse.Namespace(**vars(args))
                            branch_args.manifest = str(worktree / manifest_path)
                            with indir(worktree):
                                branch_report["checkout"] = get_checkout_identity(
                                    branch_args.manifest
                                )
                                branch_report["checkout"]["branch"] = branch
                                if is_end_of_life(branch_args.manifest):
                                    log.info("Skipping EOL runtime branch %s", branch)
                                    branch_report["status"] = "skipped"
                                    branch_report["skip_reason"] = "end-of-life"
                                    continue
                                branch_args.manifest = str(
                                    get_runtime_branch_manifest(
                                        worktree / manifest_path
                                    )
                                )
                                if (
                                    Path(branch_args.manifest).stat().st_size
                                    > args.max_manifest_size
                                ):
                                    raise ValueError(
                                        "Runtime branch manifest is too large"
                                    )
                                contents = read_manifest(branch_args.manifest)
                                if isinstance(contents, tuple):
                                    contents = contents[0]
                                if isinstance(contents, dict):
                                    parents = await runtime_eol.check(contents)
                                    if parents is not None:
                                        branch_report["status"] = "skipped"
                                        branch_report["skip_reason"] = (
                                            "runtime-end-of-life"
                                        )
                                        branch_report["runtime_eol"] = parents
                                        for parent in parents:
                                            log.info(
                                                "Skipping runtime branch %s: "
                                                "%s is EOL: %s",
                                                branch,
                                                parent["ref"],
                                                parent["end_of_life"]
                                                or parent["end_of_life_rebase"]
                                                or "No reason provided",
                                            )
                                        continue
                                (
                                    outdated,
                                    errors,
                                    updated,
                                ) = await run_manifest_with_args(
                                    branch_args, branch_report, base_branch=branch
                                )
                            if (outdated, errors, updated) == (-1, -1, False):
                                errors = 1
                                outdated = t.cast(int, branch_report.get("outdated", 0))
                        finally:
                            check_call(
                                ["git", "worktree", "remove", "--force", str(worktree)]
                            )
                except Exception as error:
                    log.error("Failed to check runtime branch %s: %s", branch, error)
                    errors += 1
                    outdated = t.cast(int, branch_report.get("outdated", outdated))
                    updated = False
                    branch_report["errors"].append(
                        {"type": type(error).__name__, "message": str(error)}
                    )
                    if branch_report["delivery"] == "pending":
                        branch_report["delivery"] = "failed"
                finally:
                    outdated_total += outdated
                    errors_total += errors
                    any_updated |= updated
                    if errors or (outdated and not updated):
                        all_updated = False
                    if errors or branch_report["errors"]:
                        branch_report["status"] = "failed"
                    elif branch_report["status"] != "skipped":
                        branch_report["status"] = "completed"
                    report.setdefault("branches", []).append(branch_report)
                    deliveries = (
                        "not_requested",
                        "no_changes",
                        "edited",
                        "committed",
                        "pr_handled",
                        "pending",
                        "failed",
                    )
                    report["delivery"] = max(
                        report["delivery"],
                        branch_report["delivery"],
                        key=deliveries.index,
                    )
            if all(branch["status"] == "skipped" for branch in report["branches"]):
                report["status"] = "skipped"
            return outdated_total, errors_total, any_updated and all_updated
    except (OSError, subprocess.CalledProcessError) as error:
        log.error("Failed to discover runtime branches: %s", error)
        report["errors"].append({"type": type(error).__name__, "message": str(error)})
        return 0, 1, False


async def run_manifest_with_args(
    args: argparse.Namespace, report: dict, base_branch: str | None = None
) -> tuple[int, int, bool]:
    if args.lint:
        errors = ManifestLinter(args.max_manifest_size).lint(Path(args.manifest))
        report["errors"] = [
            {"type": type(error).__name__, "message": str(error)} for error in errors
        ]
        for error in errors:
            log.error("%s", error)
        log.info("Lint finished with %d error(s)", len(errors))
        return 0, len(errors), False

    if (
        base_branch is None
        and not args.edit_only
        and not args.commit_to_current_branch
        and is_extension_manifest(args.manifest, args.max_manifest_size)
    ):
        result = await run_runtime_branches(args, report)
        if result is not None:
            return result

    try:
        if is_end_of_life(args.manifest):
            log.info("Skipping EOL manifest %s", args.manifest)
            report["status"] = "skipped"
            report["skip_reason"] = "end-of-life"
            return 0, 0, False
    except (OSError, ValueError) as error:
        log.error("Failed to read flathub.json: %s", error)
        report["errors"].append({"type": type(error).__name__, "message": str(error)})
        return 0, 1, False

    should_update = args.update or args.commit_only or args.edit_only
    did_update = False

    options = manifest.CheckerOptions(
        allow_unsafe=args.unsafe,
        max_manifest_size=args.max_manifest_size,
        require_important_update=args.require_important_update,
        enable_robots_txt=args.enable_robots_txt,
    )

    try:
        manifest_checker = manifest.ManifestChecker(args.manifest, options)
    except ManifestLoadError as err:
        report["errors"] = [{"type": type(err).__name__, "message": str(err)}]
        log.error("Failed to load manifest %s: %s", args.manifest, err)
        return 0, 1, False

    await manifest_checker.check(args.filter_type)
    if getattr(args, "report_json", None):
        report["sources"] = manifest_checker.get_source_report()
        report["errors"] = [
            {"type": type(error).__name__, "message": str(error)}
            for error in manifest_checker.get_report_errors()
        ]

    outdated_num = print_outdated_external_data(manifest_checker)
    report["outdated"] = outdated_num

    if should_update and outdated_num > 0:
        report["delivery"] = "pending"
        changes = manifest_checker.update_manifests()
        report["delivery"] = "edited" if changes else "no_changes"
        if changes and not args.edit_only:
            report["delivery"] = "pending"
            git_checkout = get_manifest_git_checkout(args.manifest)
            ensure_git_safe_directory(git_checkout)
            with indir(git_checkout):
                committed_changes = commit_changes(
                    changes,
                    commit_to_base=args.commit_to_current_branch,
                    base_branch=base_branch,
                )
                if not committed_changes:
                    report["delivery"] = "failed"
                    report["errors"].append(
                        {
                            "type": "CommitError",
                            "message": (
                                "Could not commit updates; see run logs for details"
                            ),
                        }
                    )
                    return (-1, -1, False)
                report["delivery"] = "committed"
                if not (args.commit_only or args.commit_to_current_branch):
                    report["delivery"] = "pending"
                    if not open_pr(
                        committed_changes,
                        manifest_checker=manifest_checker,
                        fork=args.fork,
                        pr_labels=args.pr_labels,
                        report=report,
                    ):
                        report["delivery"] = "failed"
                        report["errors"].append(
                            {
                                "type": "PublicationError",
                                "message": (
                                    "Publication failed; see run logs for details"
                                ),
                            }
                        )
                        return (-1, -1, False)
                    report["delivery"] = "pr_handled"
        did_update = bool(changes)

    if should_update and report["delivery"] == "not_requested":
        report["delivery"] = "no_changes"
    if getattr(args, "report_json", None):
        report["errors"] = [
            {"type": type(error).__name__, "message": str(error)}
            for error in manifest_checker.get_report_errors()
        ]

    errors_num = print_errors(manifest_checker)

    return outdated_num, errors_num, did_update


class ResultCode(IntFlag):
    SUCCESS = 0
    ERROR = 1
    OUTDATED = 2


def main():
    res = ResultCode.SUCCESS
    args = parse_cli_args()
    outdated_num, errors_num, updated = asyncio.run(run_with_args(args))
    if (outdated_num, errors_num, updated) == (-1, -1, False) or errors_num:
        res |= ResultCode.ERROR
    if args.check_outdated and not updated and outdated_num > 0:
        res |= ResultCode.OUTDATED
    sys.exit(res)
