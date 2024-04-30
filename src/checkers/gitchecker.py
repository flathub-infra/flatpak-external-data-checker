import logging
import re
import typing as t

import semver

from ..lib import OPERATORS_SCHEMA
from ..lib.externaldata import ExternalBase, ExternalGitRepo, ExternalGitRef
from ..lib.utils import git_ls_remote, filter_versioned_items, FallbackVersion
from ..lib.errors import CheckerQueryError, CheckerFetchError
from . import Checker

log = logging.getLogger(__name__)

REF_TAG_PREFIX = "refs/tags/"
REF_TAG_LW_SUFFIX = "^{}"


class TagWithVersion(t.NamedTuple):
    commit: str
    tag: str
    annotated: bool
    version: str

    @classmethod
    def parse_version(cls, version: str):
        return FallbackVersion(version)

    @property
    def parsed_version(self):
        return self.parse_version(self.version)

    def __lt__(self, other):
        if self.tag == other.tag:
            return self.annotated and not other.annotated
        return self.parsed_version < other.parsed_version

    def __le__(self, other):
        return self == other or self < other

    def __gt__(self, other):
        if self.tag == other.tag:
            return not self.annotated and other.annotated
        return self.parsed_version > other.parsed_version

    def __ge__(self, other):
        return self == other or self > other


class TagWithSemver(TagWithVersion):
    @classmethod
    def parse_version(self, version: str):
        return semver.VersionInfo.parse(version)


TAG_VERSION_SCHEMES = {
    "loose": TagWithVersion,
    "semantic": TagWithSemver,
}


class GitChecker(Checker):
    PRIORITY = 95
    CHECKER_DATA_TYPE = "git"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "tag-pattern": {"type": "string", "format": "regex"},
            "versions": OPERATORS_SCHEMA,
            "version-scheme": {
                "type": "string",
                "enum": list(TAG_VERSION_SCHEMES),
            },
            "sort-tags": {"type": "boolean"},
        },
    }
    SUPPORTED_DATA_CLASSES = [ExternalGitRepo]

    @classmethod
    def should_check(cls, external_data: ExternalBase):
        return isinstance(external_data, ExternalGitRepo)

    async def validate_checker_data(self, external_data: ExternalBase):
        if external_data.checker_data.get("type") == self.CHECKER_DATA_TYPE:
            return await super().validate_checker_data(external_data)
        return None

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)
        assert isinstance(external_data, ExternalGitRepo)
        if external_data.checker_data.get("type") == self.CHECKER_DATA_TYPE:
            return await self._check_has_new(external_data)
        return await self._check_still_valid(external_data)

    @staticmethod
    async def _check_has_new(external_data: ExternalGitRepo):
        tag_pattern = external_data.checker_data.get(
            "tag-pattern", r"^(?:[vV])?((?:\d+\.)+\d+)$"
        )
        tag_re = re.compile(tag_pattern)
        assert tag_re.groups == 1

        version_scheme = external_data.checker_data.get("version-scheme", "loose")
        tag_cls = TAG_VERSION_SCHEMES[version_scheme]
        sort_tags = external_data.checker_data.get("sort-tags", True)
        constraints = [
            (o, tag_cls.parse_version(v))
            for o, v in external_data.checker_data.get("versions", {}).items()
        ]

        matching_tags = []
        refs = await git_ls_remote(external_data.current_version.url)
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
            matching_tags.append(tag_cls(commit, tag, annotated, version))

        if constraints:
            sorted_tags = filter_versioned_items(
                matching_tags,
                constraints=constraints,
                to_version=lambda t: t.parsed_version,
                sort=sort_tags,
            )
        elif sort_tags:
            sorted_tags = sorted(matching_tags)
        else:
            sorted_tags = matching_tags

        try:
            latest_tag = sorted_tags[-1]
        except IndexError as err:
            raise CheckerQueryError(
                f"{external_data.current_version.url} has no tags matching "
                f"'{tag_pattern}'"
            ) from err

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
    async def _check_still_valid(external_data: ExternalGitRepo):
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

        if external_data.current_version.commit is None:
            log.info(
                "Skipping source %s, not pinned to commit",
                external_data.filename,
            )
            return

        try:
            remote_version = await external_data.current_version.fetch_remote()
        except CheckerFetchError:
            external_data.state |= external_data.State.BROKEN
            raise

        external_data.set_new_version(remote_version, is_update=False)
