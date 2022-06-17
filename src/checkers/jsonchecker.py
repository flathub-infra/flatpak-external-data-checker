import logging
import json
import re
from datetime import datetime
import typing as t
import subprocess
import os

from yarl import URL

from ..lib import utils
from ..lib.externaldata import (
    ExternalBase,
    ExternalData,
    ExternalGitRepo,
    ExternalGitRef,
)
from ..lib.errors import CheckerQueryError
from ..lib.checkers import Checker, JSONType

log = logging.getLogger(__name__)


async def _jq(query: str, data: JSONType, variables: t.Dict[str, str]) -> str:
    var_args = []
    for var_name, var_value in variables.items():
        var_args += ["--arg", var_name, var_value]

    jq_cmd = ["jq"] + var_args + ["-e", query]
    try:
        jq_stdout, _ = await utils.Command(jq_cmd).run(json.dumps(data).encode())
    except subprocess.CalledProcessError as err:
        raise CheckerQueryError("Error running jq") from err

    try:
        result = json.loads(jq_stdout)
    except json.JSONDecodeError as err:
        raise CheckerQueryError("Error reading jq output") from err

    if isinstance(result, (str, int, float)):
        return str(result)

    raise CheckerQueryError(f"Invalid jq output type {type(result)}")


def parse_timestamp(date_string: t.Optional[str]) -> t.Optional[datetime]:
    if date_string is None:
        return None
    try:
        return datetime.fromisoformat(re.sub(r"Z$", "+00:00", date_string))
    except ValueError as err:
        raise CheckerQueryError("Failed to parse timestamp") from err


class _Query(t.NamedTuple):
    name: str
    value_expr: str
    url_expr: t.Optional[str]


class JSONChecker(Checker):
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
    }
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    @classmethod
    def get_json_schema(cls, data_class: t.Type[ExternalBase]):
        schema = super().get_json_schema(data_class).copy()
        if issubclass(data_class, ExternalGitRepo):
            schema["required"] = schema.get("required", []) + [
                "tag-query",
            ]
        else:
            schema["required"] = schema.get("required", []) + [
                "version-query",
                "url-query",
            ]
        return schema

    async def _get_json(
        self,
        url: t.Union[str, URL],
        headers: t.Dict[str, str] = None,
    ) -> JSONType:
        url = URL(url)

        headers = headers.copy() if headers else {}
        if url.host == "api.github.com":
            github_token = os.environ.get("GITHUB_TOKEN")
            if github_token:
                headers["Authorization"] = f"token {github_token}"

        return await super()._get_json(url, headers)

    async def _query_sequence(
        self,
        init_json_data: JSONType,
        queries: t.Iterable[_Query],
    ) -> t.Dict[str, str]:
        results: t.Dict[str, str] = {}
        for query in queries:
            if query.url_expr:
                url = await _jq(query.url_expr, init_json_data, results)
                json_data = await self._get_json(url)
            else:
                json_data = init_json_data
            results[query.name] = await _jq(query.value_expr, json_data, results)
        return results

    @staticmethod
    def _read_q_seq(
        checker_data: t.Mapping,
        sequence: t.List[str],
    ) -> t.Iterable[_Query]:
        for query_name in sequence:
            q_prop = f"{query_name}-query"
            if q_prop not in checker_data:
                continue
            url_prop = f"{query_name}-data-url"
            yield _Query(
                name=query_name,
                value_expr=checker_data[q_prop],
                url_expr=checker_data.get(url_prop),
            )

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)

        json_url = external_data.checker_data["url"]
        json_data = await self._get_json(json_url)

        if isinstance(external_data, ExternalGitRepo):
            return await self._check_git(json_data, external_data)
        else:
            assert isinstance(external_data, ExternalData)
            return await self._check_data(json_data, external_data)

    async def _check_data(self, json_data: JSONType, external_data: ExternalData):
        checker_data = external_data.checker_data
        results = await self._query_sequence(
            json_data,
            self._read_q_seq(
                checker_data, ["tag", "commit", "version", "url", "timestamp"]
            ),
        )
        latest_version = results["version"]
        latest_url = results["url"]
        latest_timestamp = parse_timestamp(results.get("timestamp"))

        await self._update_version(
            external_data,
            latest_version,
            latest_url,
            follow_redirects=False,
            timestamp=latest_timestamp,
        )

    async def _check_git(self, json_data: JSONType, external_data: ExternalGitRepo):
        checker_data = external_data.checker_data
        results = await self._query_sequence(
            json_data,
            self._read_q_seq(checker_data, ["tag", "commit", "version", "timestamp"]),
        )
        new_version = ExternalGitRef(
            url=external_data.current_version.url,
            commit=results.get("commit"),
            tag=results["tag"],
            branch=None,
            version=results.get("version"),
            timestamp=parse_timestamp(results.get("timestamp")),
        )

        if new_version.commit is None:
            new_version = await new_version.fetch_remote()

        external_data.set_new_version(new_version)
