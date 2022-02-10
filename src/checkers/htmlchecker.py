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
from distutils.version import LooseVersion
import io
import codecs
import typing as t

import aiohttp
from yarl import URL

from ..lib import NETWORK_ERRORS
from ..lib.externaldata import ExternalBase, ExternalData
from ..lib.errors import CheckerMetadataError, CheckerQueryError, CheckerFetchError
from ..lib.checkers import Checker

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

    @staticmethod
    async def _get_encoding(response: aiohttp.ClientResponse) -> str:
        # Loosely based on aiohttp.ClientResponse.get_encoding, but
        # avoids expensive charset detection via chardet.detect() call;
        # if we didn't get a proper charset right away,
        # we're most likely facing a HTTP response that isn't textual
        ctype = response.headers.get(aiohttp.hdrs.CONTENT_TYPE, "").lower()
        mimetype = aiohttp.helpers.parse_mimetype(ctype)
        encoding = mimetype.parameters.get("charset")
        if encoding:
            try:
                codecs.lookup(encoding)
            except LookupError as err:
                raise CheckerFetchError(
                    f'Unknown encoding "{encoding}" from {response.url}'
                ) from err
        else:
            encoding = "utf-8"
        return encoding

    async def _get_text(self, url: t.Union[URL, str]) -> str:
        try:
            async with self.session.get(url) as response:
                encoding = await self._get_encoding(response)
                # We use streaming decoding in order to get decode error and abort the check
                # as early as possible, without preloading the whole raw contents into memory
                decoder_cls = codecs.getincrementaldecoder(encoding)
                decoder = decoder_cls(errors="strict")
                with io.StringIO() as buf:
                    async for chunk, _ in response.content.iter_chunks():
                        try:
                            buf.write(decoder.decode(chunk))
                        except UnicodeDecodeError as err:
                            raise CheckerQueryError from err
                    return buf.getvalue()
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
