import datetime
import logging
import re

from ..lib.checksums import MultiDigest
from ..lib.errors import CheckerQueryError
from ..lib.externaldata import ExternalBase, ExternalData, ExternalFile
from ..lib.releaselinks import http_url
from . import Checker
from .jsonchecker import parse_timestamp

log = logging.getLogger(__name__)

_JB_ARCH_MAP = {
    "x86_64": "linux",
    "aarch64": "linuxARM64",
}


class JetBrainsChecker(Checker):
    CHECKER_DATA_TYPE = "jetbrains"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "code": {"type": "string"},
            "oss": {"type": "boolean"},
            "release-type": {
                "type": "string",
                "enum": ["release", "eap", "rc", "preview"],
            },
        },
        "required": ["code"],
        "if": {"properties": {"oss": {"const": True}}, "required": ["oss"]},
        "then": {
            "properties": {
                "code": {"enum": ["IIC", "PCC"]},
                "release-type": {"const": "release"},
            }
        },
    }

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        if external_data.checker_data.get("oss", False):
            await self._check_oss(external_data)
            return

        code = external_data.checker_data["code"]
        release_type = external_data.checker_data.get("release-type", "release")

        url = "https://data.services.jetbrains.com/products/releases"
        query = {"code": code, "latest": "true", "type": release_type}

        if self.robots_cache:
            await self.robots_cache.ensure_allowed(url)

        async with self.session.get(url, params=query) as response:
            result = await response.json()
            data = result[code][0]

        release = data["downloads"][_JB_ARCH_MAP[external_data.arches[0]]]

        if self.robots_cache:
            await self.robots_cache.ensure_allowed(release["checksumLink"])

        async with self.session.get(release["checksumLink"]) as response:
            result = await response.text()
            checksum = result.split(" ")[0]

        new_version = ExternalFile(
            url=release["link"],
            checksum=MultiDigest(sha256=checksum),
            size=release["size"],
            version=data["version"],
            timestamp=datetime.datetime.strptime(data["date"], "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc
            ),
        )

        external_data.set_new_version(new_version)
        external_data.new_release_url = (
            http_url(data.get("notesLink")) if external_data.new_version else None
        )

    async def _check_oss(self, external_data: ExternalBase):
        assert isinstance(external_data, ExternalData)
        external_data.new_release_url = None
        product = {"IIC": "idea", "PCC": "pycharm"}[external_data.checker_data["code"]]
        api_url = "https://api.github.com/repos/JetBrains/intellij-community/releases"
        pattern = re.compile(rf"{product}/([0-9]+(?:\.[0-9]+)+)")
        candidates = []
        page = 1
        while True:
            releases = await self._get_json(f"{api_url}?per_page=100&page={page}")
            if not isinstance(releases, list):
                raise CheckerQueryError("JetBrains OSS: expected a release list")
            for release in releases:
                if (
                    not isinstance(release, dict)
                    or release.get("draft") is not False
                    or release.get("prerelease") is not False
                ):
                    continue
                tag = release.get("tag_name")
                match = pattern.fullmatch(tag) if isinstance(tag, str) else None
                if match:
                    version = match.group(1)
                    candidates.append(
                        (
                            tuple(int(part) for part in version.split(".")),
                            version,
                            release,
                        )
                    )
            if len(releases) < 100:
                break
            page += 1
        if not candidates:
            raise CheckerQueryError(f"JetBrains OSS: no stable {product} release")
        _, version, release = max(candidates, key=lambda candidate: candidate[0])
        arch = external_data.arches[0]
        if arch not in _JB_ARCH_MAP:
            raise CheckerQueryError(f"JetBrains OSS: unsupported architecture {arch}")
        suffix = "-aarch64" if arch == "aarch64" else ""
        name = f"{product}-{version}{suffix}.tar.gz"
        assets = release.get("assets")
        if not isinstance(assets, list):
            raise CheckerQueryError(f"JetBrains OSS: {version} has no asset list")
        matches = [
            asset
            for asset in assets
            if isinstance(asset, dict)
            and asset.get("name") == name
            and asset.get("state") == "uploaded"
        ]
        if len(matches) != 1:
            raise CheckerQueryError(
                f"JetBrains OSS: expected one uploaded asset named {name!r}"
            )
        asset = matches[0]
        url = http_url(asset.get("browser_download_url"))
        if url is None:
            raise CheckerQueryError(f"JetBrains OSS: {name} has no valid download URL")
        published_at = release.get("published_at")
        if published_at is not None and not isinstance(published_at, str):
            raise CheckerQueryError("JetBrains OSS: invalid publication date")
        timestamp = parse_timestamp(published_at)
        digest = asset.get("digest")
        size = asset.get("size")
        if (
            isinstance(digest, str)
            and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest)
            and isinstance(size, int)
            and not isinstance(size, bool)
            and size > 0
        ):
            await self._set_new_version(
                external_data,
                ExternalFile(
                    url=url,
                    checksum=MultiDigest(sha256=digest.removeprefix("sha256:")),
                    size=size,
                    version=version,
                    timestamp=timestamp,
                ),
            )
        else:
            await self._update_version(external_data, version, url, timestamp=timestamp)
        if external_data.new_version:
            external_data.new_release_url = http_url(release.get("html_url"))
