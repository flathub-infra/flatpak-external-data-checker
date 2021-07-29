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
import subprocess
import os
from datetime import datetime, timezone
from time import perf_counter
from contextlib import contextmanager

from src.lib.utils import (
    parse_github_url,
    strip_query,
    filter_versions,
    _extract_timestamp,
    Command,
)


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


class TestCommand(unittest.IsolatedAsyncioTestCase):
    _SECRET_ENV_VAR = "SOME_TOKEN_HERE"

    @contextmanager
    def _assert_timeout(self, timeout: float):
        start_time = perf_counter()
        yield start_time
        elapsed = perf_counter() - start_time
        self.assertLess(elapsed, timeout)

    def test_clear_env(self):
        os.environ[self._SECRET_ENV_VAR] = "leaked"
        cmd = Command(["printenv", self._SECRET_ENV_VAR])
        with self.assertRaises(subprocess.CalledProcessError):
            cmd.run_sync()

    async def test_clear_env_async(self):
        os.environ[self._SECRET_ENV_VAR] = "leaked"
        cmd = Command(["printenv", self._SECRET_ENV_VAR])
        with self.assertRaises(subprocess.CalledProcessError):
            await cmd.run()

    def test_timeout(self):
        cmd = Command(["sleep", "1"], timeout=0.2)
        with self._assert_timeout(0.5):
            with self.assertRaises(subprocess.TimeoutExpired):
                cmd.run_sync()

    async def test_timeout_async(self):
        cmd = Command(["sleep", "1"], timeout=0.2)
        with self._assert_timeout(0.5):
            with self.assertRaises(subprocess.TimeoutExpired):
                await cmd.run()


class TestVersionFilter(unittest.TestCase):
    def test_filter(self):
        self.assertEqual(filter_versions(["1.1"], []), ["1.1"])
        self.assertEqual(
            filter_versions(["1.1", "1.2", "1.3"], [(">", "1.0"), ("<=", "1.4")]),
            ["1.1", "1.2", "1.3"],
        )
        self.assertEqual(
            filter_versions(["1.1", "1.2", "1.3"], [("<", "1.0")]),
            [],
        )
        self.assertEqual(
            filter_versions(["1.1", "1.2", "1.3"], [("<", "1.0"), ("==", "1.2")]),
            [],
        )
        self.assertEqual(
            filter_versions(["1.1", "1.2", "1.3"], [("==", "1.2")]),
            ["1.2"],
        )
        self.assertEqual(
            filter_versions(["1.1", "1.2", "1.3"], [("!=", "1.2")]),
            ["1.1", "1.3"],
        )
        self.assertEqual(
            filter_versions(["1.a", "1.b", "1.c"], [(">=", "1.b")]),
            ["1.b", "1.c"],
        )

    def test_sort(self):
        self.assertEqual(
            filter_versions(["1.1", "1.2", "1.3"], [], sort=True),
            ["1.1", "1.2", "1.3"],
        )
        self.assertEqual(
            filter_versions(["1.3", "1.2", "1.1"], [], sort=True),
            ["1.1", "1.2", "1.3"],
        )
        self.assertEqual(
            filter_versions(["1.c", "1.a", "1.b"], [], sort=True),
            ["1.a", "1.b", "1.c"],
        )

    def test_objects(self):
        self.assertEqual(
            filter_versions(
                [("c", "1.1"), ("a", "1.3"), ("b", "1.2"), ("d", "1.0")],
                [("!=", "1.2")],
                to_string=lambda o: o[1],
                sort=True,
            ),
            [("d", "1.0"), ("c", "1.1"), ("a", "1.3")],
        )


class TestParseHTTPDate(unittest.TestCase):
    def test_parse_valid(self):
        for date_str in [
            "Wed, 20 Jan 2021 15:25:15 UTC",
            "Wed, 20-Jan-2021 15:25:15 UTC",
            "Wed, 20 Jan 2021 15:25:15 +0000",
            "Wed, 20 Jan 2021 18:25:15 +0300",
        ]:
            parsed = _extract_timestamp({"Date": date_str})
            self.assertEqual(
                parsed,
                datetime(
                    # fmt: off
                    2021, 1, 20, 15, 25, 15,
                    tzinfo=timezone.utc if parsed.tzinfo else None
                    # fmt: on
                ),
            )

    @unittest.expectedFailure
    def test_parse_invalid(self):
        self.assertIsNotNone(_extract_timestamp({"Date": "some broken string"}))


if __name__ == "__main__":
    unittest.main()
