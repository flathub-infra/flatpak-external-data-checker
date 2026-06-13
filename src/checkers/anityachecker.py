import logging
import re
from datetime import datetime, timezone

from yarl import URL

from ..lib import NETWORK_ERRORS, OPERATORS_SCHEMA
from ..lib.errors import CheckerQueryError
from ..lib.externaldata import (
    ExternalBase,
    ExternalData,
    ExternalGitRef,
    ExternalGitRepo,
)
from ..lib.utils import expand_version_constraints, filter_versions
from . import Checker

log = logging.getLogger(__name__)


def _parse_timestamp(date_string: str | None) -> datetime | None:
    # https://release-monitoring.org/api/v2/versions/?project_id=1
    # latest_version_created_on: "2022-08-20T04:31:41.440163"
    if date_string is None:
        return None
    try:
        dt = datetime.fromisoformat(re.sub(r"Z$", "+00:00", date_string))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError as err:
        raise CheckerQueryError("Failed to parse timestamp") from err


class AnityaChecker(Checker):
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
    }
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    @classmethod
    def get_json_schema(cls, data_class: type[ExternalBase]):
        schema = super().get_json_schema(data_class).copy()
        if issubclass(data_class, ExternalGitRepo):
            schema["required"] = ["project-id", "tag-template"]
        else:
            schema["required"] = ["project-id", "url-template"]
        return schema

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        instance_url = external_data.checker_data.get(
            "baseurl", "https://release-monitoring.org"
        )
        versions_url = URL(instance_url) / "api/v2/versions/"
        stable_only = external_data.checker_data.get("stable-only", True)
        constraints = expand_version_constraints(
            external_data.checker_data.get("versions", {})
        )

        query = {"project_id": external_data.checker_data["project-id"]}

        query_url = versions_url % query

        if self.robots_cache:
            await self.robots_cache.ensure_allowed(query_url)

        try:
            async with self.session.get(query_url) as response:
                result = await response.json()
        except NETWORK_ERRORS as err:
            raise CheckerQueryError from err

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

        latest_timestamp = _parse_timestamp(result.get("latest_version_created_on"))

        if isinstance(external_data, ExternalGitRepo):
            return await self._check_git(
                external_data, latest_version, latest_timestamp
            )
        assert isinstance(external_data, ExternalData)
        return await self._check_data(external_data, latest_version, latest_timestamp)

    async def _check_data(
        self,
        external_data: ExternalData,
        latest_version: str,
        latest_timestamp: datetime | None,
    ):
        url_template = external_data.checker_data["url-template"]
        latest_url = self._substitute_template(
            url_template, self._version_parts(latest_version)
        )

        await self._update_version(
            external_data,
            latest_version,
            latest_url,
            follow_redirects=False,
            timestamp=latest_timestamp,
        )

    async def _check_git(
        self,
        external_data: ExternalGitRepo,
        latest_version: str,
        latest_timestamp: datetime | None,
    ):
        tag_template = external_data.checker_data["tag-template"]
        latest_tag = self._substitute_template(
            tag_template, self._version_parts(latest_version)
        )

        new_version = await ExternalGitRef(
            url=external_data.current_version.url,
            commit=None,
            tag=latest_tag,
            branch=None,
            version=latest_version,
            timestamp=latest_timestamp,
        ).fetch_remote()

        external_data.set_new_version(new_version)
