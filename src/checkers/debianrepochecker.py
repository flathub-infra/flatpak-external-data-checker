# Debian Repo Checker: A checker that uses some metadata info from the
# manifest file in order to check whether there are newer versions of
# Debian package based external data modules.
#
# The contens of the x-checker-data for the module should be .e.g:
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
import sys
import tempfile

from lib.externaldata import ExternalData, CheckerRegistry, Checker

apt_pkg.init()

APT_NEEDED_DIRS = (
    'etc/apt/apt.conf.d', 'etc/apt/preferences.d',
    'etc/apt/trusted.gpg.d', 'var/lib/apt/lists/partial',
    'var/cache/apt/archives/partial', 'var/log/apt',
    'var/lib/dpkg', 'var/lib/dpkg/updates',
    'var/lib/dpkg/info'
)


class DebianRepoChecker(Checker):
    def __init__(self):
        self._pkgs_cache = {}

    def _should_check(self, external_data):
        return external_data.checker_data and \
               external_data.checker_data.get('type') == 'debian-repo'

    def check(self, external_data):
        # Only process external data of the debian-repo
        if not self._should_check(external_data):
            logging.debug('%s is not a debian-repo type ext data',
                          external_data.filename)
            return

        logging.debug('Checking %s', external_data.filename)
        package_name = external_data.checker_data['package-name']
        root = external_data.checker_data['root']
        dist = external_data.checker_data['dist']
        component = external_data.checker_data.get('component', '')

        if not component and not dist.endswith('/'):
            logging.warning('%s is missing Debian repo "component"; for an '
                            'exact URL, "dist" must end with /', package_name)
            return

        arch = self._translate_arch(external_data.arches[0])
        with self._load_repo(root, dist, component, arch) as cache:
            package = cache[package_name]
            candidate = package.candidate

            if candidate.sha256 != external_data.checksum:
                new_ext_data = ExternalData(external_data.type, package_name,
                                            candidate.uri, candidate.sha256,
                                            candidate.size,
                                            external_data.arches)
                new_ext_data.checker_data = external_data.checker_data
                external_data.new_version = new_ext_data

    def _translate_arch(self, arch):
        # Because architecture names in Debian differ from Flatpak's
        arches = {'x86_64': 'amd64',
                  'arm': 'armel'}
        return arches.get(arch, arch)

    @contextlib.contextmanager
    def _load_repo(self, deb_root, dist, component, arch):
        with tempfile.TemporaryDirectory() as root:
            logging.debug('Setting up apt directory structure in %s', root)

            for path in APT_NEEDED_DIRS:
                os.makedirs(os.path.join(root, path), exist_ok=True)

            # Create sources.list
            sources_list = os.path.join(root, 'etc/apt/sources.list')
            with open(sources_list, 'w') as f:
                # FIXME: import GPG key, remove 'trusted=yes' which skips GPG
                # verification
                f.write('deb [arch={arch} trusted=yes] '
                        '{deb_root} {dist} {component}\n'.format(**locals()))

            # Create empty dpkg status
            dpkg_status = os.path.join(root, 'var/lib/dpkg/status')
            with open(dpkg_status, 'w') as f:
                pass

            # Setup generic configuration
            apt_pkg.config.set('Dir', root)
            apt_pkg.config.set('Dir::State::status', dpkg_status)
            apt_pkg.config.set('Acquire::Languages', 'none')
            # FIXME: wire up progress reporting to logger, not stderr
            progress = apt.progress.text.AcquireProgress(outfile=sys.stderr)

            # Create a new cache with the appropriate architecture
            apt_pkg.config.set('APT::Architecture', arch)
            apt_pkg.config.set('APT::Architectures', arch)
            cache = apt.Cache()
            cache.update(progress)
            cache.open()

            yield cache


CheckerRegistry.register_checker(DebianRepoChecker)
