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

import bz2
import logging
import lzma
import os
import re
import urllib

from lib.externaldata import ExternalData, CheckerRegistry, Checker
from lib import utils

DEB_PACKAGES_DISTRO_URL = '{root}/dists/{dist}/{comp}/binary-{arch}/Packages'
DEB_PACKAGES_EXACT_URL = '{root}/{dist}Packages'
DEB_PACKAGES_URL_SUFFIX = ['.xz', '.bz2', '']


class PkgInfo:
    '''Represents a package in Debian's Packages repo file'''

    def __init__(self, name, arch, version, filename, checksum, size,
                 installed_size=None):
        self.name = name
        self.arch = arch
        self.version = version
        self.filename = filename
        self.checksum = checksum
        self.size = size
        self.installed_size = installed_size

    @staticmethod
    def create_from_text(data):
        def _get_value(data, key):
            lines = data.splitlines()
            for line in lines:
                if line.startswith(key + ':'):
                    return line.split(': ', 1)[1]

        name = _get_value(data, 'Package')
        arch = _get_value(data, 'Architecture')
        version = _get_value(data, 'Version')
        filename = _get_value(data, 'Filename')
        checksum = _get_value(data, 'SHA256')
        size = _get_value(data, 'Size')
        installed_size = _get_value(data, 'Installed-Size')

        return PkgInfo(name, arch, version, filename, checksum, size, installed_size)


class DebianRepoChecker(Checker):

    def __init__(self):
        self._pkgs_cache = {}

    def _should_check(self, external_data):
        return external_data.checker_data and \
               external_data.checker_data.get('type') == 'debian-repo'

    def check(self, external_data):
        # Only process external data of the debian-repo
        if not self._should_check(external_data):
            logging.debug('%s is not a debian-repo type ext data', external_data.filename)
            return

        logging.debug('Checking %s', external_data.filename)
        package_name = external_data.checker_data['package-name']
        root = external_data.checker_data['root']
        dist = external_data.checker_data['dist']
        component = external_data.checker_data.get('component', None)

        if not component and not dist.endswith('/'):
            logging.warning('%s is missing Debian repo "component", for an ' \
                            'exact URL "dist" must end with /', package_name)
            return

        arch = self._translate_arch(external_data.arches[0])
        package = self._get_package_from_url(package_name, root, dist,
                                             component, arch)

        if not package:
            return

        if package.checksum != external_data.checksum:
            url = os.path.join(root, package.filename)
            new_ext_data = ExternalData(external_data.type, package.name,
                                        url, package.checksum,
                                        package.size, external_data.arches)
            new_ext_data.checker_data = external_data.checker_data
            external_data.new_version = new_ext_data

    def _translate_arch(self, arch):
        # Because architecture names in Debian differ from Flatpak's
        arches = {'x86_64': 'amd64',
                  'arm': 'armel'}
        return arches.get(arch, arch)

    def _load_url(self, packages_url):
        if packages_url in self._pkgs_cache.keys():
            return self._pkgs_cache[packages_url]

        logging.debug('Loading contents from URL %s; '
                      'this may take a while...', packages_url)
        try:
            packages_page = utils.get_url_contents(packages_url)
        except urllib.error.HTTPError as e:
            logging.debug('Failed to load %s (%s): %s', packages_url, e.code,
                          e.reason)
            return None
        if packages_url.endswith('.xz'):
            packages_page = lzma.decompress(packages_page)
        elif packages_url.endswith('.bz2'):
            packages_page = bz2.decompress(packages_page)

        packages_page = packages_page.decode('utf-8')

        # cache the contents of the URL
        self._pkgs_cache[packages_url] = packages_page
        return packages_page

    def _split_packages_from_page(self, packages_page):
        # The packages are written in the packages_page starting by a
        # 'Package:' key and preceded by two newlines (unless it's the
        # first package). So we find all occurrences of that keyword and
        # divide the string by those indexes. This minimizes the number
        # of string divisions since the contents can be quite large.
        packages = []
        packages_indexes = [match.start() for match in
                            re.finditer('\nPackage: ', packages_page)]
        prev_index = 0
        for i in packages_indexes:
            packages.append(packages_page[prev_index:i])
            prev_index = i

        return packages

    def _get_package_from_url(self, package_name, root, dist, component, arch):
        if dist.endswith('/') and component is None:
            packages_url = DEB_PACKAGES_EXACT_URL.format(root=root, dist=dist)
        else:
            assert(component)
            packages_url = DEB_PACKAGES_DISTRO_URL.format(root=root, dist=dist,
                                                          comp=component, arch=arch)

        for suffix in DEB_PACKAGES_URL_SUFFIX:
            url = packages_url + suffix
            packages_page = self._load_url(url)
            if packages_page is not None:
                break

        if not packages_page:
            return []

        packages_data = self._split_packages_from_page(packages_page)

        logging.debug('Looking for the ext data %s in %s '
                      'packages...', package_name, len(packages_data))
        for data in packages_data:
            package = PkgInfo.create_from_text(data)
            if package.name == package_name:
                return package

        return None


CheckerRegistry.register_checker(DebianRepoChecker)
