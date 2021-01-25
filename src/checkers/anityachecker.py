import logging
import urllib.request
import urllib.parse
import json
from string import Template

from src.lib.externaldata import ExternalData, ExternalGitRepo, ExternalGitRef
from .htmlchecker import HTMLChecker

log = logging.getLogger(__name__)


class AnityaChecker(HTMLChecker):
    CHECKER_DATA_TYPE = "anitya"
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    def check(self, external_data):
        assert self.should_check(external_data)

        instance_url = external_data.checker_data.get(
            "baseurl", "https://release-monitoring.org"
        )
        project_id = external_data.checker_data.get("project-id")
        if not isinstance(project_id, int):
            log.error("Invalid type `%s` for project id", type(project_id).__name__)
            return

        project_url = urllib.parse.urljoin(instance_url, f"/api/project/{project_id}/")
        log.debug("Getting JSON from %s", project_url)
        with urllib.request.urlopen(project_url) as resp:
            result = json.load(resp)

        latest_version = result.get("version")
        if not latest_version:
            log.error("%s had no available version information", external_data.filename)
            return

        if isinstance(external_data, ExternalGitRepo):
            return self._check_git(external_data, latest_version)
        return self._check_data(external_data, latest_version)

    def _check_data(self, external_data, latest_version):
        url_template = external_data.checker_data.get("url-template")
        if not url_template:
            log.error("URL template is not set")
            return
        latest_url = Template(url_template).substitute(version=latest_version)

        self._update_version(
            external_data, latest_version, latest_url, follow_redirects=False
        )

    def _check_git(self, external_data, latest_version):
        tag_template = external_data.checker_data.get("tag-template")
        if not tag_template:
            log.error("Tag template is not set")
            return
        latest_tag = Template(tag_template).substitute(version=latest_version)

        new_version = ExternalGitRef(
            url=external_data.current_version.url,
            commit=None,
            tag=latest_tag,
            branch=None,
            version=latest_version,
            timestamp=None,
        ).fetch_remote()

        if not external_data.current_version.matches(new_version):
            external_data.new_version = new_version
            external_data.state = external_data.State.VALID
