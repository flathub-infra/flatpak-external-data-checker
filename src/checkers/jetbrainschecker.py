import datetime
import logging

from ..lib.externaldata import ExternalFile, Checker

log = logging.getLogger(__name__)


class JetBrainsChecker(Checker):
    CHECKER_DATA_TYPE = "jetbrains"

    async def check(self, external_data):
        assert self.should_check(external_data)

        code = external_data.checker_data["code"]
        release_type = external_data.checker_data.get("release-type", "release")

        url = "https://data.services.jetbrains.com/products/releases"
        query = {"code": code, "latest": "true", "type": release_type}

        async with self.session.get(url, params=query) as response:
            result = await response.json()
            data = result[code][0]

        release = data["downloads"]["linux"]

        async with self.session.get(release["checksumLink"]) as response:
            result = await response.text()
            checksum = result.split(" ")[0]

        new_version = ExternalFile(
            release["link"],
            checksum,
            release["size"],
            data["version"],
            datetime.datetime.strptime(data["date"], "%Y-%m-%d"),
        )

        external_data.set_new_version(new_version)
