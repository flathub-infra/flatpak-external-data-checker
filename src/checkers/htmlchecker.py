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
import urllib.parse
import urllib.request
from string import Template
from distutils.version import LooseVersion

from src.lib import utils
from src.lib.externaldata import ExternalData, Checker

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

    if pattern.groups != 1:
        raise ValueError(
            f"{pattern_name} {pattern} does not contain exactly 1 match group"
        )

    m = pattern.findall(html)
    if not m:
        log.debug("%s %s did not match", pattern_name, pattern)
        return None
    if len(m) == 1:
        result = m[0]
    else:
        log.debug(
            "%s %s matched multiple times, selecting latest", pattern_name, pattern
        )
        result = max(m, key=LooseVersion)

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
        try:
            url_template = external_data.checker_data["url-template"]
        except KeyError:
            log.warning("%s had no available URL", external_data.filename)
        else:
            latest_url = Template(url_template).substitute(version=latest_version)

        if not latest_version or not latest_url:
            return

        abs_url = urllib.parse.urljoin(base=url, url=latest_url)

        self._update_version(external_data, latest_version, abs_url)

    def _update_version(
        self, external_data, latest_version, latest_url, follow_redirects=True
    ):
        assert latest_version is not None
        assert latest_url is not None

        try:
            new_version, _ = utils.get_extra_data_info_from_url(
                latest_url, follow_redirects
            )
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
