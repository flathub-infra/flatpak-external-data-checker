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
import re
from pathlib import Path
from tempfile import TemporaryDirectory

import aiohttp

from src.lib.errors import CheckerError
from src.lib.utils import (
    parse_github_url,
    strip_query,
    filter_versions,
    _extract_timestamp,
    get_extra_data_info_from_url,
    Command,
    dump_manifest,
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
    _PUBLIC_ENV_VAR = "NOTHING_SPECIAL_HERE"
    _SECRET_ENV_VAR = "SOME_TOKEN_HERE"
    _TEST_ENV_VALUE = "leaked"

    def setUp(self):
        os.environ[self._PUBLIC_ENV_VAR] = self._TEST_ENV_VALUE
        os.environ[self._SECRET_ENV_VAR] = self._TEST_ENV_VALUE

    def tearDown(self):
        del os.environ[self._PUBLIC_ENV_VAR]
        del os.environ[self._SECRET_ENV_VAR]

    @contextmanager
    def _assert_timeout(self, timeout: float):
        start_time = perf_counter()
        yield start_time
        elapsed = perf_counter() - start_time
        self.assertLess(elapsed, timeout)

    def test_clear_env(self):
        cmd = Command(["printenv", self._PUBLIC_ENV_VAR])
        stdout = cmd.run_sync()[0].decode().strip()
        self.assertEqual(stdout, self._TEST_ENV_VALUE)

        cmd = Command(["printenv", self._SECRET_ENV_VAR])
        with self.assertRaises(subprocess.CalledProcessError):
            cmd.run_sync()

    async def test_clear_env_async(self):
        cmd = Command(["printenv", self._PUBLIC_ENV_VAR])
        stdout = (await cmd.run())[0].decode().strip()
        self.assertEqual(stdout, self._TEST_ENV_VALUE)

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


class TestDownload(unittest.IsolatedAsyncioTestCase):
    _CONTENT_TYPE = "application/x-fedc-test"
    http: aiohttp.ClientSession

    async def asyncSetUp(self):
        self.http = aiohttp.ClientSession(raise_for_status=True)
        await self.http.__aenter__()

    async def asyncTearDown(self):
        await self.http.close()

    async def test_wrong_content_type(self):
        with self.assertRaises(CheckerError):
            await get_extra_data_info_from_url(
                url=f"https://httpbin.org/response-headers?Content-Type={self._CONTENT_TYPE}",
                session=self.http,
                content_type_deny=[re.compile(f"^{self._CONTENT_TYPE}$")],
            )


EDITORCONFIG_SAMPLE_DATA = {"first": 1, "second": [2, 3]}
# fmt: off
EDITORCONFIG_STYLES = [
# 2-space with newline
("""
[*.json]
indent_style = space
indent_size = 2
insert_final_newline = true
""",
# ---
"""{
  "first": 1,
  "second": [
    2,
    3
  ]
}
"""),
# Tab without newline
("""
[*.json]
indent_style = tab
insert_final_newline = false
""",
# ---
"""{
	"first": 1,
	"second": [
		2,
		3
	]
}"""),
# No preference, default to 4-space without newline
(None,
# ---
"""{
    "first": 1,
    "second": [
        2,
        3
    ]
}"""),
]
# fmt: on


class TestDumpManifest(unittest.TestCase):
    tmpdir: TemporaryDirectory

    def setUp(self):
        self.tmpdir = TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_editorconfig(self):
        for i, _style in enumerate(EDITORCONFIG_STYLES):
            econfig, expected_data = _style
            path = Path(self.tmpdir.name) / str(i) / f"{i}.json"
            path.parent.mkdir(parents=True)
            if econfig:
                (path.parent / ".editorconfig").write_text(econfig)
            path.write_text("{}")  # Can be anything, just the file need to pre-exist
            dump_manifest(EDITORCONFIG_SAMPLE_DATA, path)
            self.assertEqual(path.read_text(), expected_data)


if __name__ == "__main__":
    unittest.main()
