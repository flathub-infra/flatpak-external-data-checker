import logging
import urllib.request
import os
import subprocess

from src.lib import utils
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


class JSONChecker(HTMLChecker):
    def _should_check(self, external_data):
        return external_data.checker_data.get("type") == "json"

    def check(self, external_data):
        if not self._should_check(external_data):
            log.debug("%s is not a json type ext data", external_data.filename)
            return

        json_url = external_data.checker_data["url"]
        url_query = external_data.checker_data["url-query"]
        version_query = external_data.checker_data["version-query"]

        log.debug("Getting JSON from %s", json_url)
        with urllib.request.urlopen(json_url) as resp:
            json_data = resp.read()

        latest_version = query_json(version_query, json_data)
        latest_url = query_json(url_query, json_data, {"version": latest_version})

        if not latest_version or not latest_url:
            return

        self._update_version(
            external_data, latest_version, latest_url, follow_redirects=False
        )
