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

from yarl import URL

from ..lib import (
    utils,
    NETWORK_ERRORS,
    WRONG_CONTENT_TYPES_FILE,
    WRONG_CONTENT_TYPES_ARCHIVE,
    FILE_URL_SCHEMES,
)
from ..lib.externaldata import ExternalBase, ExternalData, Checker
from ..lib.errors import CheckerMetadataError, CheckerQueryError, CheckerFetchError

log = logging.getLogger(__name__)


def _get_latest(
    html: str,
    pattern: re.Pattern,
    sort_key: t.Optional[t.Callable[[re.Match], t.Any]] = None,
) -> re.Match:
    matches = list(pattern.finditer(html))
    if not matches:
        raise CheckerQueryError(f"Pattern '{pattern.pattern}' didn't match anything")
    if sort_key is None or len(matches) == 1:
        result = matches[0]
    else:
        log.debug("%s matched multiple times, selected latest", pattern.pattern)
        result = max(matches, key=sort_key)
    log.debug("%s matched %s", pattern.pattern, result)
    return result


def _get_pattern(
    checker_data: t.Dict, pattern_name: str, expected_groups: int = 1
) -> t.Optional[re.Pattern]:
    try:
        pattern_str = checker_data[pattern_name]
    except KeyError:
        return None
    try:
        pattern = re.compile(pattern_str)
    except re.error as err:
        raise CheckerMetadataError(f"Invalid regex '{pattern_str}'") from err
    if pattern.groups != expected_groups:
        raise CheckerMetadataError(
            f"Pattern '{pattern.pattern}' contains {pattern.groups} group(s) "
            f"instead of {expected_groups}"
        )
    return pattern


class HTMLChecker(Checker):
    CHECKER_DATA_TYPE = "html"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "pattern": {"type": "string", "format": "regex"},
            "version-pattern": {"type": "string", "format": "regex"},
            "url-pattern": {"type": "string", "format": "regex"},
            "url-template": {"type": "string", "format": "regex"},
            "sort-matches": {"type": "boolean"},
        },
        "required": ["url"],
        "anyOf": [
            {"required": ["pattern"]},
            {
                "required": ["version-pattern"],
                "anyOf": [
                    {"required": ["url-pattern"]},
                    {"required": ["url-template"]},
                ],
            },
        ],
    }

    async def _get_text(self, url: t.Union[URL, str]) -> str:
        try:
            async with self.session.get(url) as response:
                return await response.text()
        except NETWORK_ERRORS as err:
            raise CheckerQueryError from err

    async def check(self, external_data: ExternalBase):
        assert self.should_check(external_data)
        assert isinstance(external_data, ExternalData)

        url = external_data.checker_data["url"]
        combo_pattern = _get_pattern(external_data.checker_data, "pattern", 2)
        version_pattern = _get_pattern(external_data.checker_data, "version-pattern", 1)
        url_pattern = _get_pattern(external_data.checker_data, "url-pattern", 1)
        url_template = external_data.checker_data.get("url-template")
        sort_matches = external_data.checker_data.get("sort-matches", True)
        assert combo_pattern or (version_pattern and (url_pattern or url_template))

        html = await self._get_text(url)

        if combo_pattern:
            latest_url, latest_version = _get_latest(
                html,
                combo_pattern,
                (lambda m: LooseVersion(m.group(2))) if sort_matches else None,
            ).group(1, 2)
        else:
            assert version_pattern
            latest_version = _get_latest(
                html,
                version_pattern,
                (lambda m: LooseVersion(m.group(1))) if sort_matches else None,
            ).group(1)
            if url_template:
                latest_url = self._substitute_placeholders(url_template, latest_version)
            else:
                assert url_pattern
                latest_url = _get_latest(
                    html,
                    url_pattern,
                    (lambda m: LooseVersion(m.group(1))) if sort_matches else None,
                ).group(1)

        abs_url = urllib.parse.urljoin(base=url, url=latest_url)

        await self._update_version(external_data, latest_version, abs_url)

    @staticmethod
    def _substitute_placeholders(template_string: str, version: str) -> str:
        version_list = LooseVersion(version).version
        tmpl = Template(template_string)
        tmpl_vars: t.Dict[str, t.Union[str, int]]
        tmpl_vars = {"version": version}
        for i, version_part in enumerate(version_list):
            tmpl_vars[f"version{i}"] = version_part
            if i == 0:
                tmpl_vars["major"] = version_part
            elif i == 1:
                tmpl_vars["minor"] = version_part
            elif i == 2:
                tmpl_vars["patch"] = version_part
        try:
            return tmpl.substitute(**tmpl_vars)
        except (KeyError, ValueError) as err:
            raise CheckerMetadataError("Error substituting template") from err

    async def _update_version(
        self,
        external_data: ExternalData,
        latest_version: str,
        latest_url: str,
        follow_redirects: bool = False,
    ):
        assert latest_version is not None
        assert latest_url is not None

        if external_data.type == ExternalData.Type.ARCHIVE:
            wrong_content_types = WRONG_CONTENT_TYPES_ARCHIVE
        else:
            wrong_content_types = WRONG_CONTENT_TYPES_FILE

        latest_url_scheme = URL(latest_url).scheme
        if latest_url_scheme not in FILE_URL_SCHEMES:
            raise CheckerMetadataError(f"Invalid URL scheme {latest_url_scheme}")

        try:
            new_version = await utils.get_extra_data_info_from_url(
                url=latest_url,
                follow_redirects=follow_redirects,
                session=self.session,
                content_type_deny=wrong_content_types,
            )
        except NETWORK_ERRORS as err:
            raise CheckerFetchError from err

        new_version = new_version._replace(
            version=latest_version  # pylint: disable=no-member
        )
        external_data.set_new_version(new_version)
