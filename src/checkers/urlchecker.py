# URL Checker: verifies if an external data URL is still accessible.  Does not need an
# x-checker-data entry and works with all external data types that have a URL. However,
# if you're dealing with a generic link that redirects to a versioned archive that
# changes, e.g.:
#
#    http://example.com/last-version -> http://example.com/prog_1.2.3.gz
#
# Then you can specify some some metadata in the manifest file to tell the checker where
# to look:
#
#   "x-checker-data": {
#       "type": "rotating-url",
#       "url": "http://example.com/last-version"
#   }
#
# Copyright © 2018-2019 Endless Mobile, Inc.
#
# Authors:
#       Joaquim Rocha <jrocha@endlessm.com>
#       Will Thompson <wjt@endlessm.com>
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
import tempfile
import typing as t

from ..lib.externaldata import ExternalData, ExternalGitRepo, Checker
from ..lib import utils, NETWORK_ERRORS
from ..lib.errors import CheckerFetchError

log = logging.getLogger(__name__)


def extract_version(checker_data, url):
    """
    If checker_data contains a "pattern", matches 'url' against it and returns the
    first capture group (which is assumed to be the version number).
    """
    try:
        pattern = checker_data["pattern"]
    except KeyError:
        return None

    m = re.match(pattern, url)
    if m is None:
        return None

    return m.group(1)


class URLChecker(Checker):
    CHECKER_DATA_TYPE = "rotating-url"
    CHECKER_DATA_SCHEMA = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "format": "uri"},
            "pattern": {"type": "string", "format": "regex"},
        },
        "required": ["url"],
    }

    def should_check(self, external_data: t.Union[ExternalData, ExternalGitRepo]):
        return isinstance(external_data, ExternalData) and (
            external_data.checker_data.get("type") == self.CHECKER_DATA_TYPE
            or external_data.type == external_data.Type.EXTRA_DATA
        )

    async def validate_checker_data(
        self, external_data: t.Union[ExternalData, ExternalGitRepo]
    ):
        if external_data.checker_data.get("type") == self.CHECKER_DATA_TYPE:
            return await super().validate_checker_data(external_data)
        return None

    async def check(self, external_data: t.Union[ExternalData, ExternalGitRepo]):
        assert self.should_check(external_data)

        is_rotating = external_data.checker_data.get("type") == self.CHECKER_DATA_TYPE
        if is_rotating:
            url = external_data.checker_data["url"]
        else:
            url = external_data.current_version.url

        if url.startswith("data:"):
            log.debug("Skipping data URL")
            return

        version_string = None

        try:
            if url.endswith(".AppImage"):
                with tempfile.TemporaryFile("w+b") as tmpfile:
                    new_version = await utils.get_extra_data_info_from_url(
                        url, session=self.session, dest_io=tmpfile
                    )
                    version_string = utils.extract_appimage_version(
                        external_data.filename,
                        tmpfile,
                    )
            else:
                new_version = await utils.get_extra_data_info_from_url(
                    url, session=self.session
                )
        except NETWORK_ERRORS as err:
            if not is_rotating:
                external_data.state = ExternalData.State.BROKEN
            raise CheckerFetchError from err

        if is_rotating and not version_string:
            version_string = extract_version(
                external_data.checker_data,
                new_version.url,
            )

        if version_string is not None:
            log.debug("%s is version %s", external_data.filename, version_string)
            new_version = new_version._replace(  # pylint: disable=no-member
                version=version_string
            )

        if not is_rotating:
            new_version = new_version._replace(url=url)  # pylint: disable=no-member

        external_data.set_new_version(
            new_version,
            is_update=(
                is_rotating and external_data.current_version.url != new_version.url
            ),
        )
