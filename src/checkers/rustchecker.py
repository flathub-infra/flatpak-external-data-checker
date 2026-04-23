import datetime
import logging
import re

import toml

from ..lib.checksums import MultiDigest
from ..lib.externaldata import ExternalBase, ExternalFile
from . import Checker

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

    def __init__(self, *args, **kwargs):
        new_args = list(args)

        # Everything is blocked
        # User-agent: *
        # Disallow: /
        if len(new_args) > 1:
            new_args[1] = None
        else:
            kwargs["robots_cache"] = None

        super().__init__(*new_args, **kwargs)

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        channel = external_data.checker_data.get("channel", "stable")
        package_name = external_data.checker_data["package"]
        target_name = external_data.checker_data["target"]

        url = f"https://static.rust-lang.org/dist/channel-rust-{channel}.toml"

        if self.robots_cache:
            await self.robots_cache.ensure_allowed(url)

        async with self.session.get(url) as response:
            data = toml.loads(await response.text())

        package = data["pkg"][package_name]
        target = package["target"][target_name]

        release_date = datetime.datetime.fromisoformat(data["date"])
        version_match = VERSION_RE.match(package["version"])
        assert version_match
        version, _, _ = version_match.groups()
        if channel == "nightly":
            appstream_version = f"{version}-{release_date:%Y%m%d}"
        else:
            appstream_version = version

        if target["available"]:
            new_version = ExternalFile(
                url=target["xz_url"],
                checksum=MultiDigest(sha256=target["xz_hash"]),
                size=None,
                version=appstream_version,
                timestamp=release_date,
            )
            external_data.set_new_version(new_version)
