# GNOME Checker: A checker to see if the url is pointing to the latest Release.
#
# Consult the README for information on how to use this checker.
#
# Copyright © 2020 Maximiliano Sandoval <msandova@protonmail.com>
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
from distutils.version import LooseVersion

from src.lib import utils
from src.lib.externaldata import ExternalData, Checker

log = logging.getLogger(__name__)


def get_latest(package_name, skip_unstable):
    """
    If checker_data contains a "name", matches 'cache.json' against it and
    returns the latest version without and with its minor release
    """
    url = "https://download.gnome.org/sources/{}/cache.json".format(
        package_name)

    try:
        pattern = re.compile(
            "([\\d.]+\\d)/{}-([\\d.]+\\d).tar.xz".format(package_name))
    except KeyError:
        return None

    if pattern.groups != 2:
        raise ValueError(
            f"{pattern} does not contain exactly 2 match group"
        )

    resp = urllib.request.urlopen(url)
    html = resp.read().decode()

    m = pattern.findall(html)
    if not m:
        log.debug("%s did not match", pattern)
        return None
    if len(m) == 1:
        result = m[0]
    else:
        log.debug(
            "%s matched multiple times, selecting latest", pattern
        )
        versions = [x[0] for x in m if is_stable(x[1], skip_unstable)]
        short_versions = [x[1] for x in m if is_stable(x[1], skip_unstable)]
        result = (
            max(versions, key=LooseVersion),
            max(short_versions, key=LooseVersion)
        )

    log.debug("%s matched: %s",  pattern, result[1])
    return result


def is_stable(version, skip_unstable):
    """
    If "skip-unstable" is set to True, returns False  if version matches
    3.x.y with x and odd number, or x.y.z for y in "alpha", "beta", "rc"
    """
    if skip_unstable:
        pattern = "3.([\\d]+).[\\d]+"
        z = re.match(pattern, version)
        if z:
            v = int(z.groups()[0])
            if v % 2 == 1:
                log.debug("Ignored unstable version: %s", version)
                return False
        new_pattern = "([\\d]+).([\\w]+).[\\d]+"
        w = re.match(new_pattern, version)
        if w:
            mayor = int(w.groups()[0])
            minor = w.groups()[1]
            if minor in ["alpha", "beta", "rc"] and mayor >= 40:
                log.debug("Ignored unstable version: %s", version)
                return False
    return True


class GNOMEChecker(Checker):
    def _should_check(self, external_data):
        return external_data.checker_data.get("type") == "gnome"

    def check(self, external_data):

        skip_unstable = external_data.checker_data.get("skip-unstable")
        if not self._should_check(external_data):
            log.debug("%s is not a GNOME type ext data",
                      external_data.filename)
            return
        name = external_data.checker_data["name"]
        url = "https://download.gnome.org/sources/{}/cache.json".format(name)
        log.debug("Getting extra data info from %s; may take a while", url)

        latest_version_short, latest_version = get_latest(name, skip_unstable)
        latest_url = "https://download.gnome.org/sources/{}/{}/{}-{}.tar.xz".format(
            name, latest_version_short, name, latest_version
        )

        if not latest_version:
            log.warning(
                "%s had no available version information",
                external_data.filename
            )

        if not latest_version or not latest_url:
            return

        assert latest_version is not None

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
            new_version = new_version._replace(url=latest_url)
            if not external_data.current_version.matches(new_version):
                external_data.new_version = new_version
