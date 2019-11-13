# HTML Checker: A checker to see if the url is pointing to the latest HTML Player.
#
# Consult the README for information on how to use this checker.
#
# Copyright © 2019 Bastien Nocera <hadess@hadess.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import logging
import re
import urllib.error
import urllib.request
import urllib.parse

from src.lib.externaldata import ExternalData, Checker
from src.lib import utils

log = logging.getLogger(__name__)


def get_latest(checker_data, pattern_name, html):
    """
    If checker_data contains a "pattern", matches 'html' against it and returns the
    first capture group (which is assumed to be the version or URL).
    """
    try:
        pattern = re.compile(checker_data[pattern_name])
    except KeyError:
        return None

    m = pattern.search(html)
    if m is None:
        log.debug("%s %s did not match", pattern_name, pattern)
        return None

    result = m.group(1)
    log.debug("%s %s matched: %s", pattern_name, pattern, result)
    return result


class HTMLChecker(Checker):
    def _should_check(self, external_data):
        return external_data.checker_data.get("type") == "html"

    def check(self, external_data):
        if not self._should_check(external_data):
            log.debug("%s is not a html type ext data", external_data.filename)
            return

        url = external_data.checker_data.get("url")
        log.debug("Getting extra data info from %s; may take a while", url)
        resp = urllib.request.urlopen(url)
        html = resp.read().decode()

        latest_version = get_latest(external_data.checker_data, "version-pattern", html)
        latest_url = get_latest(external_data.checker_data, "url-pattern", html)
        if not latest_version:
            log.warning(
                "%s had no available version information", external_data.filename
            )
        if not latest_url:
            log.warning("%s had no available URL", external_data.filename)
        if not latest_version or not latest_url:
            return

        assert latest_version is not None
        assert latest_url is not None

        try:
            new_version, _ = utils.get_extra_data_info_from_url(latest_url)
        except urllib.error.HTTPError as e:
            log.warning("%s returned %s", latest_url, e)
            external_data.state = ExternalData.State.BROKEN
        except Exception:
            log.exception("Unexpected exception while checking %s", latest_url)
            external_data.state = ExternalData.State.BROKEN
        else:
            external_data.state = ExternalData.State.VALID
            new_version = new_version._replace(version=latest_version)
            if not external_data.current_version.matches(new_version):
                external_data.new_version = new_version
