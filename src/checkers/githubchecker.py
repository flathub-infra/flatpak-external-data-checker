from typing import Any

from ..lib.errors import CheckerMetadataError, CheckerQueryError
from ..lib.externaldata import (
    CHECKER_DATA_SCHEMA_COMMON,
    ExternalBase,
    ExternalData,
    ExternalGitRef,
    ExternalGitRepo,
)
from ..lib.releaselinks import api_release_url, github_release_api_url, http_url
from . import Checker
from .jsonchecker import parse_timestamp


class GitHubChecker(Checker):
    CHECKER_DATA_TYPE = "github"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            **CHECKER_DATA_SCHEMA_COMMON["properties"],
            "type": {"const": "github"},
            "repo": {
                "type": "string",
                "pattern": r"^[A-Za-z0-9_-]+/[A-Za-z0-9_.-]+$",
            },
            "tag-pattern": {"type": "string", "format": "regex"},
            "asset-name": {"type": "string", "minLength": 1},
            "source-archive": {"enum": ["tarball", "zipball"]},
            "enable-robots-txt": {"type": "boolean"},
        },
        "required": ["type", "repo"],
        "additionalProperties": False,
    }
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    @classmethod
    def get_json_schema(cls, data_class: type[ExternalBase]) -> dict[str, Any]:
        schema = cls.CHECKER_DATA_SCHEMA.copy()
        selectors = [{"required": [name]} for name in ("asset-name", "source-archive")]
        if issubclass(data_class, ExternalGitRepo):
            schema["not"] = {"anyOf": selectors}
        else:
            schema["oneOf"] = selectors
        return schema

    async def check(self, external_data: ExternalBase) -> None:
        assert self.should_check(external_data)
        external_data.new_release_url = None
        metadata = external_data.checker_data
        repo = metadata["repo"]
        repository_url = f"https://github.com/{repo}"
        api_url = f"https://api.github.com/repos/{repo}/releases/latest"
        if isinstance(external_data, ExternalGitRepo):
            source_api = github_release_api_url(
                external_data.current_version.url, "HEAD"
            )
            repo_api = f"https://api.github.com/repos/{repo}/releases/tags/HEAD"
            if not source_api or source_api.lower() != repo_api.lower():
                raise CheckerMetadataError(
                    f"Git source must be an HTTP(S) URL for GitHub repository {repo}"
                )

        release = await self._get_json(api_url)
        if (
            not isinstance(release, dict)
            or release.get("draft") is not False
            or release.get("prerelease") is not False
        ):
            raise CheckerQueryError(f"{repo}: expected a published stable release")
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not tag:
            raise CheckerQueryError(f"{repo}: release has no tag name")
        version = tag
        pattern = self._get_pattern(metadata, "tag-pattern")
        if pattern is not None:
            match = pattern.fullmatch(tag)
            if match is None or not match.group(1):
                raise CheckerQueryError(
                    f"{repo}: latest release tag {tag!r} "
                    f"does not match {pattern.pattern!r}"
                )
            version = match.group(1)
        published_at = release.get("published_at")
        if published_at is not None and not isinstance(published_at, str):
            raise CheckerQueryError(f"{repo}: invalid release publication date")
        timestamp = parse_timestamp(published_at)

        url = None
        if isinstance(external_data, ExternalGitRepo):
            ref = ExternalGitRef(
                url=external_data.current_version.url,
                commit=None,
                tag=tag,
                branch=None,
                version=version,
                timestamp=timestamp,
            )
            external_data.set_new_version(await ref.fetch_remote())
        else:
            assert isinstance(external_data, ExternalData)
            if "source-archive" in metadata:
                url = http_url(release.get(f"{metadata['source-archive']}_url"))
            else:
                name = self._substitute_template(
                    metadata["asset-name"], self._version_parts(version) | {"tag": tag}
                )
                assets = release.get("assets")
                if not isinstance(assets, list):
                    raise CheckerQueryError(
                        f"{repo}: release {tag!r} has no asset list"
                    )
                matches = [
                    asset
                    for asset in assets
                    if isinstance(asset, dict)
                    and asset.get("name") == name
                    and asset.get("state") == "uploaded"
                ]
                if len(matches) != 1:
                    raise CheckerQueryError(
                        f"{repo}: release {tag!r}: expected one uploaded asset "
                        f"named {name!r}, found {len(matches)}"
                    )
                url = http_url(matches[0].get("browser_download_url"))
            if url is None:
                raise CheckerQueryError(
                    f"{repo}: release {tag!r} has no valid download URL"
                )
            await self._update_version(external_data, version, url, timestamp=timestamp)

        if external_data.new_version is not None:
            external_data.new_release_url = api_release_url(
                api_url,
                release,
                tag=tag,
                asset_url=url,
                repository_url=repository_url,
            )
