import logging
import urllib.request
import urllib.parse

import requests

from ..lib.externaldata import ExternalData, ExternalGitRepo, ExternalGitRef
from .htmlchecker import HTMLChecker

log = logging.getLogger(__name__)


class AnityaChecker(HTMLChecker):
    CHECKER_DATA_TYPE = "anitya"
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    async def check(self, external_data):
        assert self.should_check(external_data)

        instance_url = external_data.checker_data.get(
            "baseurl", "https://release-monitoring.org"
        )
        versions_url = urllib.request.urljoin(instance_url, "/api/v2/versions/")
        stable_only = external_data.checker_data.get("stable-only", False)

        query = {"project_id": external_data.checker_data.get("project-id")}
        with requests.get(versions_url, params=query) as response:
            response.raise_for_status()
            result = response.json()

        if stable_only:
            latest_version = result["stable_versions"][0]
        else:
            latest_version = result["latest_version"]

        if isinstance(external_data, ExternalGitRepo):
            return await self._check_git(external_data, latest_version)
        return await self._check_data(external_data, latest_version)

    async def _check_data(self, external_data, latest_version):
        url_template = external_data.checker_data["url-template"]
        latest_url = self._substitute_placeholders(url_template, latest_version)

        await self._update_version(
            external_data, latest_version, latest_url, follow_redirects=False
        )

    async def _check_git(self, external_data, latest_version):
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
