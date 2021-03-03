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
import urllib.parse
from string import Template
from distutils.version import LooseVersion
import typing as t

import requests

from ..lib import utils
from ..lib.externaldata import ExternalData, Checker

log = logging.getLogger(__name__)


def _get_latest(
    html: str, pattern: re.Pattern, sort_key=t.Optional[t.Callable]
) -> t.Optional[t.Union[str, t.Tuple[str, ...]]]:
    match = pattern.findall(html)
    if not match:
        log.warning("%s did not match", pattern.pattern)
        return None
    if sort_key is None or len(match) == 1:
        result = match[0]
    else:
        log.debug("%s matched multiple times, selected latest", pattern.pattern)
        result = max(match, key=sort_key)
    log.debug("%s matched %s", pattern.pattern, result)
    return result


def _get_pattern(checker_data: t.Dict, pattern_name: str):
    try:
        return re.compile(checker_data[pattern_name])
    except KeyError:
        return None


class HTMLChecker(Checker):
    CHECKER_DATA_TYPE = "html"

    def check(self, external_data):
        assert self.should_check(external_data)

        url = external_data.checker_data["url"]
        combo_pattren = _get_pattern(external_data.checker_data, "pattern")
        version_pattern = _get_pattern(external_data.checker_data, "version-pattern")
        url_pattern = _get_pattern(external_data.checker_data, "url-pattern")
        url_template = external_data.checker_data.get("url-template")
        sort_matches = external_data.checker_data.get("sort-matches", True)
        assert combo_pattren or (version_pattern and (url_pattern or url_template))

        with requests.get(url) as response:
            response.raise_for_status()
            html = response.text

        latest_version: t.Optional[str] = None
        latest_url: t.Optional[str] = None

        if combo_pattren:
            assert combo_pattren.groups == 2
            latest_pair = _get_latest(
                html,
                combo_pattren,
                (lambda m: LooseVersion(m[1])) if sort_matches else None,
            )
            if latest_pair:
                latest_url, latest_version = latest_pair
        elif version_pattern:
            assert version_pattern.groups == 1
            latest_version = _get_latest(
                html, version_pattern, LooseVersion if sort_matches else None
            )
            if latest_version and url_template:
                latest_url = Template(url_template).substitute(version=latest_version)
            elif url_pattern:
                assert url_pattern.groups == 1
                latest_url = _get_latest(
                    html, url_pattern, LooseVersion if sort_matches else None
                )

        if not latest_version or not latest_url:
            log.warning(
                "Couldn't get version and/or URL for %s", external_data.filename
            )
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
        except (
            requests.exceptions.HTTPError,
            requests.exceptions.ConnectionError,
        ) as e:
            log.warning("%s returned %s", latest_url, e)
            external_data.state = ExternalData.State.BROKEN
        else:
            new_version = new_version._replace(  # pylint: disable=no-member
                version=latest_version
            )
            external_data.set_new_version(new_version)
