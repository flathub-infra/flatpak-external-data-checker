import logging
import urllib.request
import urllib.parse
import json
from string import Template

from .htmlchecker import HTMLChecker

log = logging.getLogger(__name__)


class AnityaChecker(HTMLChecker):
    CHECKER_DATA_TYPE = "anitya"

    def check(self, external_data):
        assert self.should_check(external_data)

        instance_url = external_data.checker_data.get(
            "baseurl", "https://release-monitoring.org"
        )
        try:
            project_id = external_data.checker_data["project-id"]
            url_template = external_data.checker_data["url-template"]
        except KeyError as e:
            log.error("Missing required key: %s", e)
            return
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
        latest_url = Template(url_template).substitute(version=latest_version)

        self._update_version(
            external_data, latest_version, latest_url, follow_redirects=False
        )
