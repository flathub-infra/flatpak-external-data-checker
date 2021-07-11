import logging
import os
import re
from datetime import datetime
import typing as t
import asyncio

from ..lib import utils
from ..lib.externaldata import ExternalData, ExternalGitRepo, ExternalGitRef
from .htmlchecker import HTMLChecker

log = logging.getLogger(__name__)


async def query_json(query, data, variables=None):
    typecheck_q = (
        '.|type as $rt | if $rt=="string" or $rt=="number" then . else error($rt) end'
    )

    var_args = []
    if variables is not None:
        for var_name, var_value in variables.items():
            var_args += ["--arg", var_name, var_value]

    jq_cmd = ["jq"] + var_args + ["-r", "-e", f"( {query} ) | ( {typecheck_q} )"]
    if utils.check_bwrap():
        jq_cmd = utils.wrap_in_bwrap(jq_cmd, bwrap_args=["--die-with-parent"])

    jq_proc = await asyncio.create_subprocess_exec(
        *jq_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        env=utils.clear_env(os.environ),
    )
    jq_stdout, _ = await jq_proc.communicate(input=data)
    assert jq_proc.returncode == 0
    return jq_stdout.decode().strip()


async def query_sequence(json_data, queries):
    results = {}
    for result_key, query in queries:
        if not query:
            continue
        results[result_key] = await query_json(query, json_data, results)
    return results


def parse_timestamp(date_string: t.Optional[str]) -> t.Optional[datetime]:
    if date_string is None:
        return None
    try:
        return datetime.fromisoformat(re.sub(r"Z$", "+00:00", date_string))
    except ValueError as err:
        log.error("Failed to parse timestamp %s: %s", date_string, err)
        return None


class JSONChecker(HTMLChecker):
    CHECKER_DATA_TYPE = "json"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "tag-query": {"type": "string"},
            "commit-query": {"type": "string"},
            "version-query": {"type": "string"},
            "url-query": {"type": "string"},
            "timestamp-query": {"type": "string"},
        },
        "required": ["url"],
        "anyOf": [
            {"required": ["version-query", "url-query"]},
            {"required": ["tag-query"]},
        ],
    }
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    async def check(self, external_data):
        assert self.should_check(external_data)

        json_url = external_data.checker_data["url"]
        async with self.session.get(json_url) as response:
            json_data = await response.read()

        if isinstance(external_data, ExternalGitRepo):
            return await self._check_git(json_data, external_data)
        else:
            return await self._check_data(json_data, external_data)

    async def _check_data(self, json_data: bytes, external_data: ExternalData):
        checker_data = external_data.checker_data
        results = await query_sequence(
            json_data,
            [
                ("tag", checker_data.get("tag-query")),
                ("commit", checker_data.get("commit-query")),
                ("version", checker_data["version-query"]),
                ("url", checker_data["url-query"]),
            ],
        )
        latest_version = results["version"]
        latest_url = results["url"]

        await self._update_version(
            external_data, latest_version, latest_url, follow_redirects=False
        )

    async def _check_git(self, json_data: bytes, external_data: ExternalGitRepo):
        checker_data = external_data.checker_data
        results = await query_sequence(
            json_data,
            [
                ("tag", checker_data["tag-query"]),
                ("commit", checker_data.get("commit-query")),
                ("version", checker_data.get("version-query")),
                ("timestamp", checker_data.get("timestamp-query")),
            ],
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
