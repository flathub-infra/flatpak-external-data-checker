import datetime
import toml
import logging
import urllib.request

from src.lib import utils
from src.lib.externaldata import ExternalFile, Checker

log = logging.getLogger(__name__)


class RustChecker(Checker):
    @staticmethod
    def _should_check(external_data):
        return external_data.checker_data.get("type") == "rust"

    def check(self, external_data):
        if not self._should_check(external_data):
            log.debug("%s is not a rust type ext data", external_data.filename)
            return

        channel = external_data.checker_data.get("channel", "stable")
        package_name = external_data.checker_data["package"]
        target_name = external_data.checker_data["target"]

        url = f"https://static.rust-lang.org/dist/channel-rust-{channel}.toml"

        log.debug("Getting extra data info from %s; may take a while", url)
        with urllib.request.urlopen(url) as response:
            data = toml.loads(response.read().decode())

        package = data["pkg"][package_name]
        target = package["target"][target_name]
        if target["available"]:
            new_version = ExternalFile(
                target["xz_url"],
                target["xz_hash"],
                None,
                package["version"],
                datetime.datetime.strptime(data["date"], "%Y-%m-%d"),
            )
            if not external_data.current_version.matches(new_version):
                external_data.new_version = new_version
