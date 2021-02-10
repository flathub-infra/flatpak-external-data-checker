import logging
import urllib.request
import os
import subprocess

from src.lib import utils
from src.lib.externaldata import ExternalData, ExternalGitRepo, ExternalGitRef
from .htmlchecker import HTMLChecker

log = logging.getLogger(__name__)


def query_json(query, data, variables=None):
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

    jq_proc = subprocess.run(
        jq_cmd,
        check=True,
        stdout=subprocess.PIPE,
        input=data,
        timeout=10,
        env=utils.clear_env(os.environ),
    )
    return jq_proc.stdout.decode().strip()


def query_sequence(json_data, queries):
    results = {}
    for result_key, query in queries:
        if not query:
            continue
        results[result_key] = query_json(query, json_data, results)
    return results


class JSONChecker(HTMLChecker):
    CHECKER_DATA_TYPE = "json"
    SUPPORTED_DATA_CLASSES = [ExternalData, ExternalGitRepo]

    def check(self, external_data):
        assert self.should_check(external_data)

        json_url = external_data.checker_data["url"]

        log.debug("Getting JSON from %s", json_url)
        with urllib.request.urlopen(json_url) as resp:
            json_data = resp.read()

        if isinstance(external_data, ExternalGitRepo):
            return self._check_git(json_data, external_data)
        else:
            return self._check_data(json_data, external_data)

    def _check_data(self, json_data: str, external_data: ExternalData):
        checker_data = external_data.checker_data
        results = query_sequence(
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

        self._update_version(
            external_data, latest_version, latest_url, follow_redirects=False
        )

    def _check_git(self, json_data: str, external_data: ExternalGitRepo):
        checker_data = external_data.checker_data
        results = query_sequence(
            json_data,
            [
                ("tag", checker_data["tag-query"]),
                ("commit", checker_data.get("commit-query")),
                ("version", checker_data.get("version-query")),
            ],
        )
        new_version = ExternalGitRef(
            external_data.current_version.url,
            results.get("commit"),
            results["tag"],
            None,
            results.get("version"),
            None,
        )

        if new_version.commit is None:
            new_version = new_version.fetch_remote()

        external_data.set_new_version(new_version)
