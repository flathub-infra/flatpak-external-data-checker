import logging
import re
from datetime import datetime
import typing as t
import subprocess

from ..lib import utils, NETWORK_ERRORS
from ..lib.externaldata import (
    ExternalBase,
    ExternalData,
    ExternalGitRepo,
    ExternalGitRef,
)
from ..lib.errors import CheckerQueryError
from .htmlchecker import HTMLChecker

log = logging.getLogger(__name__)


async def query_json(query: str, data: bytes, variables: t.Dict[str, str]) -> str:
    typecheck_q = (
        '.|type as $rt | if $rt=="string" or $rt=="number" then . else error($rt) end'
    )

    var_args = []
    for var_name, var_value in variables.items():
        var_args += ["--arg", var_name, var_value]

    jq_cmd = ["jq"] + var_args + ["-r", "-e", f"( {query} ) | ( {typecheck_q} )"]
    try:
        jq_stdout, _ = await utils.Command(jq_cmd).run(data)
    except subprocess.CalledProcessError as err:
        raise CheckerQueryError("Error running jq") from err
    return jq_stdout.decode().strip()


def parse_timestamp(date_string: t.Optional[str]) -> t.Optional[datetime]:
    if date_string is None:
        return None
    try:
        return datetime.fromisoformat(re.sub(r"Z$", "+00:00", date_string))
    except ValueError as err:
        raise CheckerQueryError("Failed to parse timestamp") from err


class JSONChecker(HTMLChecker):
    CHECKER_DATA_TYPE = "json"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "tag-query": {"type": "string"},
            "tag-data-url": {"type": "string"},
            "commit-query": {"type": "string"},
            "commit-data-url": {"type": "string"},
            "version-query": {"type": "string"},
            "version-data-url": {"type": "string"},
            "url-query": {"type": "string"},
            "url-data-url": {"type": "string"},
            "timestamp-query": {"type": "string"},
            "timestamp-data-url": {"type": "string"},
        },
        "required": ["url"],
        "anyOf": [
            {"required": ["version-query", "url-query"]},
            {"required": ["tag-query"]},
        ],
    }
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    async def _get_json(self, url: str) -> bytes:
        log.debug("Get JSON from %s", url)
        try:
            async with self.session.get(url) as response:
                return await response.read()
        except NETWORK_ERRORS as err:
            raise CheckerQueryError from err

    async def _query_sequence(
        self,
        json_data: bytes,
        queries: t.Iterable[t.Tuple[str, str, t.Optional[str]]],
    ) -> t.Dict[str, str]:
        results: t.Dict[str, str] = {}
        for result_key, value_query, url_query in queries:
            if url_query:
                url = await query_json(url_query, json_data, results)
                json_data = await self._get_json(url)
            results[result_key] = await query_json(value_query, json_data, results)
        return results

    @staticmethod
    def _read_q_seq(checker_data: t.Mapping, sequence: t.List[str]):
        for query_name in sequence:
            q_prop = f"{query_name}-query"
            if q_prop not in checker_data:
                continue
            url_prop = f"{query_name}-data-url"
            yield (query_name, checker_data[q_prop], checker_data.get(url_prop))

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        json_url = external_data.checker_data["url"]
        json_data = await self._get_json(json_url)

        if isinstance(external_data, ExternalGitRepo):
            return await self._check_git(json_data, external_data)
        else:
            assert isinstance(external_data, ExternalData)
            return await self._check_data(json_data, external_data)

    async def _check_data(self, json_data: bytes, external_data: ExternalData):
        checker_data = external_data.checker_data
        results = await self._query_sequence(
            json_data,
            self._read_q_seq(checker_data, ["tag", "commit", "version", "url"]),
        )
        latest_version = results["version"]
        latest_url = results["url"]

        await self._update_version(
            external_data, latest_version, latest_url, follow_redirects=False
        )

    async def _check_git(self, json_data: bytes, external_data: ExternalGitRepo):
        checker_data = external_data.checker_data
        results = await self._query_sequence(
            json_data,
            self._read_q_seq(checker_data, ["tag", "commit", "version", "timestamp"]),
        )
        new_version = ExternalGitRef(
            external_data.current_version.url,
            results.get("commit"),
            results["tag"],
            None,
            results.get("version"),
            parse_timestamp(results.get("timestamp")),
        )

        if new_version.commit is None:
            new_version = await new_version.fetch_remote()

        external_data.set_new_version(new_version)
