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

import os
import pkgutil


class Checker:

    def check(self, external_data):
        raise NotImplementedError()


class CheckerRegistry:

    _checkers = []

    @staticmethod
    def load(checkers_folder):
        for _unused, modname, _unused in pkgutil.walk_packages([checkers_folder]):
            pkg_name = os.path.basename(checkers_folder)
            __import__(pkg_name + '.' + modname)

    @classmethod
    def register_checker(class_, checker):
        if not issubclass(checker, Checker):
            raise TypeError('{} is not a of type {}'.format(checker, Checker))

        class_._checkers.append(checker)

    @classmethod
    def get_checkers(class_):
        return class_._checkers


from lib.cvedata import CVEData
from lib.externaldata import ExternalData
