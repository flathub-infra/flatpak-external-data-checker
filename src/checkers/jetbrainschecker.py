import datetime
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from src.lib.externaldata import ExternalFile, Checker

log = logging.getLogger(__name__)


class JetBrainsChecker(Checker):
    CHECKER_DATA_TYPE = "jetbrains"

    def check(self, external_data):
        assert self.should_check(external_data)

        code = external_data.checker_data["code"]
        release_type = external_data.checker_data.get("release-type", "release")

        url = f"https://data.services.jetbrains.com/products/releases?code={code}&latest=true&type={release_type}"

        log.debug("Getting extra data info from %s; may take a while", url)
        resp = urllib.request.urlopen(url)
        data = json.load(resp)[code][0]
        release = data["downloads"]["linux"]

        checksum = (
            urllib.request.urlopen(release["checksumLink"])
            .read()
            .decode("utf-8")
            .split(" ")[0]
        )

        new_version = ExternalFile(
            release["link"],
            checksum,
            release["size"],
            data["version"],
            datetime.datetime.strptime(data["date"], "%Y-%m-%d"),
        )

        if not external_data.current_version.matches(new_version):
            external_data.new_version = new_version
