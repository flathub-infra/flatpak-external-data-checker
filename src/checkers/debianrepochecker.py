# Debian Repo Checker: A checker that uses some metadata info from the
# manifest file in order to check whether there are newer versions of
# Debian package based external data modules.
#
# The contents of the x-checker-data for the module should be .e.g:
#   "x-checker-data": {
#                       "type": "debian-repo",
#                       "package-name": "YOUR_PACKAGE_NAME",
#                       "root": "ROOT_URL_TO_THE_DEBIAN_REPO",
#                       "dist": "DEBIAN_DIST",
#                       "component": "DEBIAN_COMPONENT"
#                     }
#
# Copyright (C) 2018 Endless Mobile, Inc.
#
# Authors:
#       Joaquim Rocha <jrocha@endlessm.com>
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

import apt
import apt_pkg
import contextlib
import logging
import os
import tempfile

from src.lib.externaldata import Checker, ExternalFile
from src.lib.utils import get_timestamp_from_url

apt_pkg.init()

APT_NEEDED_DIRS = (
    "etc/apt/apt.conf.d",
    "etc/apt/preferences.d",
    "etc/apt/trusted.gpg.d",
    "var/lib/apt/lists/partial",
    "var/cache/apt/archives/partial",
    "var/log/apt",
    "var/lib/dpkg",
    "var/lib/dpkg/updates",
    "var/lib/dpkg/info",
)

LOG = logging.getLogger(__name__)


class LoggerAcquireProgress(apt.progress.text.AcquireProgress):
    def __init__(self, logger):
        class FileLike:
            def write(self, text):
                text = text.strip()
                if text:  # ignore write("\r")
                    logger.debug(text)

            def flush(self):
                pass

            # no fileno() to avoid SIGWINCH stuff

        super().__init__(FileLike())

    def pulse(self, owner):
        """Disable percentage reporting within files."""
        return apt.progress.base.AcquireProgress.pulse(self, owner)


def _get_timestamp_for_candidate(candidate):
    # TODO: fetch package, parse changelog, get the date from there.
    # python-apt can fetch changelogs from Debian and Ubuntu's changelog
    # server, but most packages this checker will be used for are not from these repos.
    # We'd have to open-code it.
    # https://salsa.debian.org/apt-team/python-apt/blob/master/apt/package.py#L1245-1417
    return get_timestamp_from_url(candidate.uri)


class DebianRepoChecker(Checker):
    def __init__(self):
        self._pkgs_cache = {}

    def _should_check(self, external_data):
        return external_data.checker_data.get("type") == "debian-repo"

    def check(self, external_data):
        # Only process external data of the debian-repo
        if not self._should_check(external_data):
            LOG.debug("%s is not a debian-repo type ext data", external_data.filename)
            return

        LOG.debug("Checking %s", external_data.filename)
        package_name = external_data.checker_data["package-name"]
        root = external_data.checker_data["root"]
        dist = external_data.checker_data["dist"]
        component = external_data.checker_data.get("component", "")

        if not component and not dist.endswith("/"):
            LOG.warning(
                '%s is missing Debian repo "component"; for an '
                'exact URL, "dist" must end with /',
                package_name,
            )
            return

        arch = self._translate_arch(external_data.arches[0])
        with self._load_repo(root, dist, component, arch) as cache:
            package = cache[package_name]
            candidate = package.candidate
            new_version = ExternalFile(
                candidate.uri,
                candidate.sha256,
                candidate.size,
                candidate.version,
                timestamp=_get_timestamp_for_candidate(candidate),
            )

            if not external_data.current_version.matches(new_version):
                external_data.new_version = new_version

    def _translate_arch(self, arch):
        # Because architecture names in Debian differ from Flatpak's
        arches = {"x86_64": "amd64", "arm": "armel"}
        return arches.get(arch, arch)

    @contextlib.contextmanager
    def _load_repo(self, deb_root, dist, component, arch):
        with tempfile.TemporaryDirectory() as root:
            LOG.debug("Setting up apt directory structure in %s", root)

            for path in APT_NEEDED_DIRS:
                os.makedirs(os.path.join(root, path), exist_ok=True)

            # Create sources.list
            sources_list = os.path.join(root, "etc/apt/sources.list")
            with open(sources_list, "w") as f:
                # FIXME: import GPG key, remove 'trusted=yes' which skips GPG
                # verification
                f.write(
                    "deb [arch={arch} trusted=yes] "
                    "{deb_root} {dist} {component}\n".format(**locals())
                )

            # Create empty dpkg status
            dpkg_status = os.path.join(root, "var/lib/dpkg/status")
            with open(dpkg_status, "w") as f:
                pass

            # Setup generic configuration
            apt_pkg.init()
            apt_pkg.config.set("Dir", root)
            apt_pkg.config.set("Dir::State::status", dpkg_status)
            apt_pkg.config.set("Acquire::Languages", "none")
            progress = LoggerAcquireProgress(LOG)

            # Create a new cache with the appropriate architecture
            apt_pkg.config.set("APT::Architecture", arch)
            apt_pkg.config.set("APT::Architectures", arch)
            cache = apt.Cache()
            cache.update(progress)
            cache.open()

            yield cache
