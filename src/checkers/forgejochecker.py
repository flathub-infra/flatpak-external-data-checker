from urllib.parse import urlsplit

from ..lib.errors import CheckerMetadataError, CheckerQueryError
from ..lib.externaldata import (
    CHECKER_DATA_SCHEMA_COMMON,
    ExternalBase,
    ExternalData,
    ExternalGitRef,
    ExternalGitRepo,
)
from ..lib.releaselinks import api_release_url, forgejo_repository_path, http_url
from . import Checker
from .jsonchecker import parse_timestamp


class ForgejoChecker(Checker):
    CHECKER_DATA_TYPE = "forgejo"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            **CHECKER_DATA_SCHEMA_COMMON["properties"],
            "type": {"const": "forgejo"},
            "instance": {"type": "string", "pattern": r"^https?://[^\s?#@]+$"},
            "repo": {
                "type": "string",
                "pattern": r"^(?!\.{1,2}/)[A-Za-z0-9_.-]+/(?!\.{1,2}$)[A-Za-z0-9_.-]+$",
            },
            "tag-pattern": {"type": "string", "format": "regex"},
            "asset-name": {"type": "string", "minLength": 1},
            "source-archive": {"enum": ["tarball", "zipball"]},
            "enable-robots-txt": {"type": "boolean"},
        },
        "required": ["type", "instance", "repo"],
        "additionalProperties": False,
    }
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    @classmethod
    def get_json_schema(cls, data_class: type[ExternalBase]):
        schema = cls.CHECKER_DATA_SCHEMA.copy()
        selectors = [{"required": [name]} for name in ("asset-name", "source-archive")]
        if issubclass(data_class, ExternalGitRepo):
            schema["not"] = {"anyOf": selectors}
        else:
            schema["oneOf"] = selectors
        return schema

    @staticmethod
    def validate_instance(value: str) -> str:
        try:
            parsed = urlsplit(value)
            if (
                not http_url(value)
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or any(c.isspace() for c in value)
                or "\\" in value
            ):
                raise ValueError
            _ = parsed.port  # Validate the optional port before making requests.
        except ValueError as err:
            raise CheckerMetadataError("instance must be an HTTP(S) base URL") from err
        return value.rstrip("/")

    async def validate_checker_data(self, external_data: ExternalBase):
        await super().validate_checker_data(external_data)
        self.validate_instance(external_data.checker_data["instance"])

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)
        external_data.new_release_url = None
        metadata = external_data.checker_data
        instance = self.validate_instance(metadata["instance"])
        repo = metadata["repo"]
        repository_url = f"{instance}/{repo}"
        api_url = f"{instance}/api/v1/repos/{repo}/releases/latest"
        if isinstance(external_data, ExternalGitRepo):
            source = urlsplit(external_data.current_version.url)
            expected = urlsplit(repository_url)
            if (
                source.scheme not in ("http", "https")
                or source.netloc.lower() != expected.netloc.lower()
                or forgejo_repository_path(source.path.rstrip("/").removesuffix(".git"))
                != forgejo_repository_path(expected.path)
                or source.query
                or source.fragment
            ):
                raise CheckerMetadataError(
                    f"Git source must be an HTTP(S) URL for {repository_url}"
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
                    f"{repo}: latest release tag {tag!r} does not match"
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
                    if isinstance(asset, dict) and asset.get("name") == name
                ]
                if len(matches) != 1:
                    raise CheckerQueryError(
                        f"{repo}: release {tag!r}: expected one asset "
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
                api_url, release, tag=tag, asset_url=url, repository_url=repository_url
            )
