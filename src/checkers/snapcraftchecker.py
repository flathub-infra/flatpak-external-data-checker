import urllib.request
import datetime
import json
import logging

from src.lib.externaldata import ExternalFile, Checker

log = logging.getLogger(__name__)


class SnapcraftChecker(Checker):
    arches = {"x86_64": "amd64", "aarch64": "arm64", "arm": "armhf", "i386": "i386"}

    @staticmethod
    def _should_check(external_data):
        return external_data.checker_data.get("type") == "snapcraft"

    def check(self, external_data):
        if not self._should_check(external_data):
            log.debug("%s is not a snapcraft type ext data", external_data.filename)
            return

        name = external_data.checker_data["name"]
        channel = external_data.checker_data["channel"]

        url = f"http://api.snapcraft.io/v2/snaps/info/{name}"
        header = {"Snap-Device-Series": "16"}

        req = urllib.request.Request(url, headers=header)

        log.debug("Getting extra data info from %s; may take a while", url)
        resp = urllib.request.urlopen(req)

        d = json.load(resp)

        data = [
            x
            for x in d["channel-map"]
            if x["channel"]["architecture"] == self.arches[external_data.arches[0]]
            and x["channel"]["name"] == channel
        ][0]

        new_version = ExternalFile(
            data["download"]["url"],
            data["download"]["sha3-384"],
            data["download"]["size"],
            data["version"],
            datetime.datetime.strptime(
                data["channel"]["released-at"], "%Y-%m-%dT%H:%M:%S.%f%z"
            ),
        )

        if not external_data.current_version.matches(new_version):
            external_data.new_version = new_version
