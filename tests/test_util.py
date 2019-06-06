#!/usr/bin/env python3
# Copyright © 2019 Endless Mobile, Inc.
#
# Authors:
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
import os
import unittest
import sys

# Yuck!
tests_dir = os.path.dirname(__file__)
checker_path = os.path.join(tests_dir, "..", "src")
sys.path.append(checker_path)

from lib.utils import parse_github_url


class TestParseGitHubUrl(unittest.TestCase):
    def test_ssh(self):
        self.assertEqual(
            parse_github_url(
                "git@github.com:endlessm/flatpak-external-data-checker.git"
            ),
            "endlessm/flatpak-external-data-checker",
        )

    def test_https(self):
        self.assertEqual(
            parse_github_url("https://github.com/flathub/com.dropbox.Client"),
            "flathub/com.dropbox.Client",
        )


if __name__ == "__main__":
    unittest.main()
