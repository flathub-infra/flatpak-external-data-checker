import logging
import re
import typing as t
from distutils.version import LooseVersion

from src.lib.externaldata import Checker, ExternalGitRepo, ExternalGitRef
from src.lib.utils import git_ls_remote

log = logging.getLogger(__name__)

REF_TAG_PREFIX = "refs/tags/"
REF_TAG_LW_SUFFIX = "^{}"


class TagWithVersion(t.NamedTuple):
    commit: str
    tag: str
    annotated: bool
    version: str

    def __lt__(self, other):
        if self.tag == other.tag:
            return self.annotated and not other.annotated
        return LooseVersion(self.version) < LooseVersion(other.version)

    def __gt__(self, other):
        if self.tag == other.tag:
            return not self.annotated and other.annotated
        return LooseVersion(self.version) > LooseVersion(other.version)


class GitChecker(Checker):
    CHECKER_DATA_TYPE = "git"
    SUPPORTED_DATA_CLASSES = [ExternalGitRepo]

    def should_check(self, external_data):
        return isinstance(external_data, ExternalGitRepo)

    def check(self, external_data):
        assert self.should_check(external_data)
        external_data: ExternalGitRepo
        if external_data.checker_data.get("type") == self.CHECKER_DATA_TYPE:
            return self._check_has_new(external_data)
        return self._check_still_valid(external_data)

    @staticmethod
    def _check_has_new(external_data):
        tag_pattern = external_data.checker_data.get(
            "tag-pattern", r"^(?:[vV])?(\d[\d\w.-]+\d)"
        )
        tag_re = re.compile(tag_pattern)
        assert tag_re.groups == 1

        matching_tags = []
        refs = git_ls_remote(external_data.current_version.url)
        for ref, commit in refs.items():
            if not ref.startswith(REF_TAG_PREFIX):
                continue
            tag = ref[len(REF_TAG_PREFIX) :]
            if tag.endswith(REF_TAG_LW_SUFFIX):
                annotated = False
                tag = tag[: -len(REF_TAG_LW_SUFFIX)]
            else:
                annotated = True
            tag_match = tag_re.match(tag)
            if not tag_match:
                continue
            version = tag_match.group(1)
            matching_tags.append(TagWithVersion(commit, tag, annotated, version))

        if external_data.checker_data.get("sort-tags", True):
            sorted_tags = sorted(matching_tags)
        else:
            sorted_tags = matching_tags
        latest_tag = sorted_tags[-1]

        new_version = ExternalGitRef(
            url=external_data.current_version.url,
            commit=latest_tag.commit,
            tag=latest_tag.tag,
            branch=None,
            version=latest_tag.version,
            timestamp=None,
        )
        external_data.set_new_version(new_version)

    @staticmethod
    def _check_still_valid(external_data):
        if (
            external_data.current_version.commit is not None
            and external_data.current_version.tag is None
            and external_data.current_version.branch is None
        ):
            log.info(
                "Skipping source %s, commit is specified, but neither tag nor branch",
                external_data.filename,
            )
            return

        if (
            external_data.current_version.commit is None
            and external_data.current_version.tag is None
        ):
            log.info(
                "Skipping source %s, not pinned to tag or commit",
                external_data.filename,
            )
            return

        try:
            remote_version = external_data.current_version.fetch_remote()
        except KeyError as err:
            log.error(
                "Couldn't get remote commit from %s: not found %s",
                external_data.current_version.url,
                err,
            )
            external_data.state = ExternalGitRepo.State.BROKEN
            return

        external_data.set_new_version(remote_version, is_update=False)
