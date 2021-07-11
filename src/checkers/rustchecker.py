import datetime
import logging
import re

import toml

from ..lib.externaldata import ExternalFile, Checker

log = logging.getLogger(__name__)


VERSION_RE = re.compile(r"^(\S+)\s+\((\S+)\s+(\S+)\)")


class RustChecker(Checker):
    CHECKER_DATA_TYPE = "rust"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "channel": {"type": "string", "enum": ["stable", "beta", "nightly"]},
            "package": {"type": "string"},
            "target": {"type": "string"},
        },
        "required": ["package", "target"],
    }

    async def check(self, external_data):
        assert self.should_check(external_data)

        channel = external_data.checker_data.get("channel", "stable")
        package_name = external_data.checker_data["package"]
        target_name = external_data.checker_data["target"]

        url = f"https://static.rust-lang.org/dist/channel-rust-{channel}.toml"

        async with self.session.get(url) as response:
            data = toml.loads(await response.text())

        package = data["pkg"][package_name]
        target = package["target"][target_name]

        release_date = datetime.date.fromisoformat(data["date"])
        version, _, _ = VERSION_RE.match(package["version"]).groups()
        if channel == "nightly":
            appstream_version = "{0}-{1:%Y%m%d}".format(version, release_date)
        else:
            appstream_version = version

        if target["available"]:
            new_version = ExternalFile(
                target["xz_url"],
                target["xz_hash"],
                None,
                appstream_version,
                release_date,
            )
            external_data.set_new_version(new_version)
