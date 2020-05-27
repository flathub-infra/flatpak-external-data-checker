#!/usr/bin/env python3
# Copyright © 2019–2020 Endless Mobile, Inc.
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
import unittest

from src.lib.utils import parse_github_url, strip_query


class TestParseGitHubUrl(unittest.TestCase):
    def test_ssh(self):
        url = "git@github.com:flathub/flatpak-external-data-checker.git"
        self.assertEqual(parse_github_url(url), "flathub/flatpak-external-data-checker")

    def test_ssh_no_dotgit(self):
        url = "git@github.com:flathub/flatpak-external-data-checker"
        self.assertEqual(parse_github_url(url), "flathub/flatpak-external-data-checker")

    def test_https(self):
        url = "https://github.com/flathub/com.dropbox.Client"
        self.assertEqual(parse_github_url(url), "flathub/com.dropbox.Client")

    def test_https_with_auth(self):
        url = "https://acce55ed:x-oauth-basic@github.com/endlessm/eos-google-chrome-app"
        self.assertEqual(parse_github_url(url), "endlessm/eos-google-chrome-app")


class TestStripQuery(unittest.TestCase):
    def test_strip_query(self):
        url = "https://d11yldzmag5yn.cloudfront.net/prod/3.5.372466.0322/zoom_x86_64.tar.xz?_x_zm_rtaid=muDd1uOqSZ-xUScZF698QQ.1585134521724.21e5ab14908b2121f5ed53882df91cb9&_x_zm_rhtaid=732"  # noqa: E501
        expected = "https://d11yldzmag5yn.cloudfront.net/prod/3.5.372466.0322/zoom_x86_64.tar.xz"
        self.assertEqual(strip_query(url), expected)

    def test_preserve_nice_query(self):
        url = "https://dl2.tlauncher.org/f.php?f=files%2FTLauncher-2.69.zip"
        expected = url
        self.assertEqual(strip_query(url), expected)

    def test_preserve_auth(self):
        url = "https://user:pass@example.com/"
        expected = url
        self.assertEqual(strip_query(url), expected)


if __name__ == "__main__":
    unittest.main()
