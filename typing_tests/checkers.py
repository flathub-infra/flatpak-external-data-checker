from collections.abc import Iterable
from datetime import datetime
from types import MappingProxyType
from typing import Any

from typing_extensions import assert_type

from src.checkers.electronchecker import ElectronChecker
from src.checkers.forgejochecker import ForgejoChecker
from src.checkers.githubchecker import GitHubChecker
from src.checkers.htmlchecker import HTMLChecker
from src.checkers.jsonchecker import JSONChecker, _jq, _Query, parse_timestamp
from src.checkers.urlchecker import extract_version
from src.lib.checksums import MultiDigest
from src.lib.externaldata import ExternalData, ExternalGitRepo
from src.lib.releaselinks import _asset_urls


async def checker_contracts(
    html: HTMLChecker,
    json: JSONChecker,
) -> None:
    assert_type(await html._get_text("https://example.com"), str)
    assert_type(await json._query_sequence([], {}), dict[str, str])
    assert_type(await _jq(".version", {"version": "2"}, {}), str)


def schema_contracts() -> None:
    assert_type(GitHubChecker.get_json_schema(ExternalData), dict[str, Any])
    assert_type(ForgejoChecker.get_json_schema(ExternalGitRepo), dict[str, Any])
    assert_type(JSONChecker.get_json_schema(ExternalData), dict[str, Any])
    assert_type(JSONChecker.get_json_schema(ExternalGitRepo), dict[str, Any])
    assert_type(
        JSONChecker._read_q_seq({"version-query": ".version"}, ["version"]),
        Iterable[_Query],
    )
    assert_type(parse_timestamp(None), datetime | None)
    assert_type(extract_version({}, "https://example.com"), str | None)
    assert_type(parse_timestamp("2026-11-04T00:00:00Z"), datetime | None)
    assert_type(
        ElectronChecker._read_digests(MappingProxyType({"sha512": "YWJj"})),
        MultiDigest,
    )
    assert_type(
        _asset_urls(MappingProxyType({"tarball_url": "https://example.com/a"})),
        list[object],
    )
