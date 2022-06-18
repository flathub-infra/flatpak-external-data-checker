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
import io
import codecs
import typing as t

import aiohttp
from yarl import URL
import semver

from ..lib import NETWORK_ERRORS, OPERATORS_SCHEMA
from ..lib.externaldata import ExternalBase, ExternalData
from ..lib.errors import CheckerMetadataError, CheckerQueryError, CheckerFetchError
from ..lib.checkers import Checker
from ..lib.utils import filter_versioned_items, FallbackVersion

log = logging.getLogger(__name__)


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


def _semantic_version(version: str) -> semver.VersionInfo:
    try:
        return semver.VersionInfo.parse(version)
    except ValueError as err:
        raise CheckerQueryError("Can't parse version") from err


_VERSION_SCHEMES = {
    "loose": FallbackVersion,
    "semantic": _semantic_version,
}


class HTMLChecker(Checker):
    CHECKER_DATA_TYPE = "html"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "pattern": {"type": "string", "format": "regex"},
            "version-pattern": {"type": "string", "format": "regex"},
            "url-template": {"type": "string", "format": "regex"},
            "sort-matches": {"type": "boolean"},
            "versions": OPERATORS_SCHEMA,
            "version-scheme": {
                "type": "string",
                "enum": list(_VERSION_SCHEMES),
            },
        },
        "allOf": [
            {"required": ["url"]},
            {
                "if": {"required": ["version-pattern"]},
                "then": {"required": ["url-template"]},
                "else": {"required": ["pattern"]},
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

        url_tmpl = external_data.checker_data["url"]

        if external_data.parent:
            assert isinstance(external_data.parent, ExternalBase)
            parent_state = (
                external_data.parent.new_version or external_data.parent.current_version
            )
            parent_json = parent_state.json
            if parent_state.version:
                parent_json |= self._version_parts(parent_state.version)
        else:
            parent_json = {}

        url = self._substitute_template(
            url_tmpl,
            {f"parent_{k}": v for k, v in parent_json.items() if v is not None},
        )

        combo_pattern = _get_pattern(external_data.checker_data, "pattern", 2)
        version_pattern = _get_pattern(external_data.checker_data, "version-pattern", 1)
        url_template = external_data.checker_data.get("url-template")
        sort_matches = external_data.checker_data.get("sort-matches", True)
        version_cls = _VERSION_SCHEMES[
            external_data.checker_data.get("version-scheme", "loose")
        ]
        constraints = [
            (o, version_cls(v))
            for o, v in external_data.checker_data.get("versions", {}).items()
        ]
        assert combo_pattern or (version_pattern and url_template)

        html = await self._get_text(url)

        def _get_latest(pattern: re.Pattern, ver_group: int) -> re.Match:
            matches = filter_versioned_items(
                items=pattern.finditer(html),
                constraints=constraints,
                to_version=lambda m: version_cls(m.group(ver_group)),
                sort=sort_matches,
            )
            if not matches:
                raise CheckerQueryError(
                    f"Pattern '{pattern.pattern}' didn't match anything"
                )

            try:
                # NOTE Returning last match when sort is requested and first match otherwise
                # doesn't seem sensible, but we need to retain backward compatibility
                result = matches[-1 if sort_matches else 0]
            except IndexError as err:
                raise CheckerQueryError(
                    f"Pattern '{pattern.pattern}' didn't match anything"
                ) from err

            log.debug("%s matched %s", pattern.pattern, result)
            return result

        if combo_pattern:
            latest_url, latest_version = _get_latest(combo_pattern, 2).group(1, 2)
        else:
            assert version_pattern and url_template
            latest_version = _get_latest(version_pattern, 1).group(1)
            latest_url = self._substitute_template(
                url_template, self._version_parts(latest_version)
            )

        abs_url = urllib.parse.urljoin(base=url, url=latest_url)

        await self._update_version(external_data, latest_version, abs_url)
