import logging

from yarl import URL

from ..lib import OPERATORS_SCHEMA
from ..lib.externaldata import (
    ExternalBase,
    ExternalData,
    ExternalGitRepo,
    ExternalGitRef,
)
from ..lib.utils import filter_versions
from ..lib.errors import CheckerQueryError
from .htmlchecker import HTMLChecker

log = logging.getLogger(__name__)


class AnityaChecker(HTMLChecker):
    CHECKER_DATA_TYPE = "anitya"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "baseurl": {"type": "string"},
            "project-id": {"type": "number"},
            "stable-only": {"type": "boolean"},
            "versions": OPERATORS_SCHEMA,
            "url-template": {"type": "string"},
            "tag-template": {"type": "string"},
        },
        "anyOf": [
            {"required": ["project-id", "url-template"]},
            {"required": ["project-id", "tag-template"]},
        ],
    }
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        instance_url = external_data.checker_data.get(
            "baseurl", "https://release-monitoring.org"
        )
        versions_url = URL(instance_url) / "api/v2/versions/"
        stable_only = external_data.checker_data.get("stable-only", False)
        constraints = external_data.checker_data.get("versions", {}).items()

        query = {"project_id": external_data.checker_data["project-id"]}
        async with self.session.get(versions_url % query) as response:
            result = await response.json()

        if stable_only or constraints:
            if stable_only:
                versions = result["stable_versions"]
            else:
                versions = result["versions"]
            if constraints:
                versions = filter_versions(versions, constraints, sort=False)
            try:
                latest_version = versions[0]
            except IndexError as err:
                raise CheckerQueryError("Can't find matching version") from err
        else:
            latest_version = result["latest_version"]

        if isinstance(external_data, ExternalGitRepo):
            return await self._check_git(external_data, latest_version)
        assert isinstance(external_data, ExternalData)
        return await self._check_data(external_data, latest_version)

    async def _check_data(self, external_data: ExternalData, latest_version):
        url_template = external_data.checker_data["url-template"]
        latest_url = self._substitute_placeholders(url_template, latest_version)

        await self._update_version(
            external_data, latest_version, latest_url, follow_redirects=False
        )

    async def _check_git(self, external_data: ExternalGitRepo, latest_version):
        tag_template = external_data.checker_data["tag-template"]
        latest_tag = self._substitute_placeholders(tag_template, latest_version)

        new_version = await ExternalGitRef(
            url=external_data.current_version.url,
            commit=None,
            tag=latest_tag,
            branch=None,
            version=latest_version,
            timestamp=None,
        ).fetch_remote()

        external_data.set_new_version(new_version)
