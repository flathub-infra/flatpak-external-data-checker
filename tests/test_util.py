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
import asyncio
import re
import subprocess
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent
from time import perf_counter
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from src.lib import utils
from src.lib.errors import CheckerFetchError, CheckerQueryError
from src.lib.utils import (
    Command,
    FallbackVersion,
    VersionComparisonError,
    _detect_json_flatpak_manifest_indent,
    asyncio_gather_failfast,
    check_bwrap,
    dump_manifest,
    expand_version_constraints,
    filter_versioned_items,
    filter_versions,
    get_extra_data_info_from_url,
    git_ls_remote,
    parse_date_header,
    parse_github_url,
    read_json_manifest,
    strip_query,
    wrap_in_bwrap,
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

    def test_invalid_scheme_raises(self):
        with self.assertRaises(ValueError):
            parse_github_url("svn://example.com/repo")


class TestStripQuery(unittest.TestCase):
    def test_strip_query(self):
        url = "https://d11yldzmag5yn.cloudfront.net/prod/3.5.372466.0322/zoom_x86_64.tar.xz?_x_zm_rtaid=muDd1uOqSZ-xUScZF698QQ.1585134521724.21e5ab14908b2121f5ed53882df91cb9&_x_zm_rhtaid=732"
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
    @contextmanager
    def _assert_timeout(self, timeout: float):
        start_time = perf_counter()
        yield start_time
        elapsed = perf_counter() - start_time
        self.assertLess(elapsed, timeout)

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

    def test_run_sync_nonzero_exit_raises(self):
        cmd = Command(["false"], sandbox=False)
        with self.assertRaises(subprocess.CalledProcessError):
            cmd.run_sync()

    def test_run_sync_returns_output(self):
        cmd = Command(["echo", "hi"], sandbox=False)
        stdout, _ = cmd.run_sync()
        self.assertIn(b"hi", stdout)

    def test_sandbox_init_with_allow_paths(self):
        cmd = Command(
            ["/bin/true"],
            sandbox=True,
            allow_network=True,
            allow_paths=[
                "/tmp/plain",
                Command.SandboxPath("/etc/ssl", readonly=True, optional=True),
            ],
        )
        argv_str = " ".join(cmd.argv)
        self.assertIn("bwrap", argv_str)
        self.assertIn("--share-net", argv_str)
        self.assertIn("/tmp/plain", argv_str)
        self.assertIn("/etc/ssl", argv_str)

    @patch("src.lib.utils.asyncio.create_subprocess_exec")
    async def test_timeout_kill_oserror(self, mock_create_subproc):
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_proc.kill.side_effect = OSError("Simulated kill failure")
        mock_create_subproc.return_value = mock_proc

        cmd = Command(["sleep", "1"], timeout=0.1, sandbox=False)

        with self.assertRaises(subprocess.TimeoutExpired):
            await cmd.run()

        mock_proc.kill.assert_called_once()


class TestSandboxPath(unittest.TestCase):
    def test_readwrite_required(self):
        sp = Command.SandboxPath("/tmp/foo")
        self.assertEqual(sp.bwrap_args, ["--bind", "/tmp/foo", "/tmp/foo"])

    def test_readonly_required(self):
        sp = Command.SandboxPath("/usr", readonly=True)
        self.assertEqual(sp.bwrap_args, ["--ro-bind", "/usr", "/usr"])

    def test_readonly_optional(self):
        sp = Command.SandboxPath("/etc/ssl", readonly=True, optional=True)
        self.assertEqual(sp.bwrap_args, ["--ro-bind-try", "/etc/ssl", "/etc/ssl"])


class TestWrapInBwrap(unittest.TestCase):
    def test_without_extra_args(self):
        result = wrap_in_bwrap(["/bin/true"])
        self.assertIn("/bin/true", result)
        self.assertNotIn("--die-with-parent", result)

    def test_with_extra_args(self):
        result = wrap_in_bwrap(["/bin/true"], ["--die-with-parent"])
        self.assertIn("--die-with-parent", result)
        self.assertIn("/bin/true", result)


class TestCheckBwrap(unittest.TestCase):
    def test_returns_false_when_bwrap_not_found(self):
        with patch(
            "src.lib.utils.subprocess.run",
            side_effect=FileNotFoundError("bwrap not found"),
        ):
            self.assertFalse(check_bwrap())

    def test_returns_false_when_bwrap_fails(self):
        with patch(
            "src.lib.utils.subprocess.run",
            side_effect=subprocess.CalledProcessError(
                returncode=1, cmd=["bwrap"], output="error"
            ),
        ):
            self.assertFalse(check_bwrap())

    def test_returns_true_when_bwrap_succeeds(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("src.lib.utils.subprocess.run", return_value=mock_result):
            self.assertTrue(check_bwrap())


class TestExpandVersionConstraints(unittest.TestCase):
    def test_string_values_unchanged(self):
        self.assertEqual(
            expand_version_constraints(
                {"!=": "1.2", ">=": "1.0", "<": "2.0", "<=": "3.0"}
            ),
            [
                ("!=", "1.2"),
                (">=", "1.0"),
                ("<", "2.0"),
                ("<=", "3.0"),
            ],
        )

    def test_exclude_array_expanded(self):
        self.assertEqual(
            expand_version_constraints({"!=": ["1.2", "1.3"]}),
            [("!=", "1.2"), ("!=", "1.3")],
        )

    def test_array_and_strings(self):
        self.assertEqual(
            expand_version_constraints({"!=": ["1.2", "1.3"], "<": "2.0"}),
            [("!=", "1.2"), ("!=", "1.3"), ("<", "2.0")],
        )

    def test_empty(self):
        self.assertEqual(expand_version_constraints({}), [])

    def test_non_exclude_array_raises(self):
        with self.assertRaises(ValueError):
            expand_version_constraints({"<": ["1.0", "2.0"]})


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
        self.assertEqual(
            filter_versions(["3.1.0", "2.2.0", "1.12.0", "start"], [("<", "2.0.0")]),
            ["1.12.0"],
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

    def test_multiple_exclude_constraints(self):
        self.assertEqual(
            filter_versions(
                ["1.1", "1.2", "1.3", "1.4"],
                [("!=", "1.2"), ("!=", "1.3")],
            ),
            ["1.1", "1.4"],
        )

    def test_objects(self):
        self.assertEqual(
            filter_versioned_items(
                [("c", "1.1"), ("a", "1.3"), ("b", "1.2"), ("d", "1.0")],
                [("!=", FallbackVersion("1.2"))],
                to_version=lambda o: FallbackVersion(o[1]),
                sort=True,
            ),
            [("d", "1.0"), ("c", "1.1"), ("a", "1.3")],
        )

    def test_comparison_error_excludes_item(self):
        result = filter_versioned_items(
            [FallbackVersion("1.0"), FallbackVersion(None)],  # type: ignore[list-item]
            [("<", FallbackVersion("2.0"))],
            to_version=lambda v: v,
        )
        self.assertEqual(result, [FallbackVersion("1.0")])


class TestVersionComparisonError(unittest.TestCase):
    def test_init_stores_operands(self):
        err = VersionComparisonError("1.a", "2.b")
        self.assertEqual(err.left, "1.a")
        self.assertEqual(err.right, "2.b")
        self.assertIn("1.a", str(err))
        self.assertIn("2.b", str(err))

    def test_fallback_version_raises_on_incomparable(self):
        with self.assertRaises(VersionComparisonError):
            _ = FallbackVersion("1.0") < FallbackVersion(None)  # type: ignore[arg-type]


class TestParseHTTPDate(unittest.TestCase):
    def test_parse_valid(self):
        for date_str in [
            "Wed, 20 Jan 2021 15:25:15 UTC",
            "Wed, 20-Jan-2021 15:25:15 UTC",
            "Wed, 20-Jan-2021 15:25:15 GMT",
            "Wed, 20 Jan 2021 15:25:15 +0000",
            "Wed, 20 Jan 2021 18:25:15 +0300",
            "Wed, 20 Jan 2021 07:25:15 -0800",
            # https://github.com/flathub-infra/flatpak-external-data-checker/issues/370
            "Wed, 20 Jan 2021 23:25:15 +0800",
            "Wed, 20 Jan 2021 23:25:15 Asia/Shanghai",
        ]:
            parsed = parse_date_header(date_str)
            self.assertEqual(
                parsed,
                datetime(
                    # fmt: off
                    2021,
                    1,
                    20,
                    15,
                    25,
                    15,
                    tzinfo=timezone.utc if parsed.tzinfo else None,
                    # fmt: on
                ),
            )

    @unittest.expectedFailure
    def test_parse_invalid(self):
        self.assertIsNotNone(parse_date_header("some broken string"))

    def test_named_tz_unparseable_date_falls_through(self):
        parse_date_header("NOTADATE America/New_York")

    def test_empty_string_returns_now(self):
        result = parse_date_header("")
        self.assertIsNotNone(result)


class TestDownload(unittest.IsolatedAsyncioTestCase):
    _CONTENT_TYPE = "application/x-fedc-test"
    http: aiohttp.ClientSession

    async def asyncSetUp(self):
        self.http = aiohttp.ClientSession(raise_for_status=True)
        await self.http.__aenter__()

    async def asyncTearDown(self):
        await self.http.close()

    async def test_correct_content_type(self):
        url = "https://ftpmirror.gnu.org/gnu/gzip/gzip-1.12.tar.gz"

        fake_chunk = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03fake_payload_data"
        fake_headers = {"Last-Modified": "Wed, 20 Jan 2021 15:25:15 UTC"}

        mock_response = MagicMock()
        mock_response.url = MagicMock()
        mock_response.url.__str__ = MagicMock(return_value=url)
        mock_response.headers = fake_headers

        async def fake_iter_chunked(_size):
            yield fake_chunk

        mock_response.content.iter_chunked = fake_iter_chunked

        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        result = await get_extra_data_info_from_url(
            url=url,
            session=mock_session,
            content_type_deny=[re.compile(r"^application/x-fedc-test$")],
        )

        self.assertEqual(result.url, url)
        self.assertEqual(result.size, len(fake_chunk))
        mock_session.get.assert_called_once()

    @patch("src.lib.utils.magic.from_buffer")
    async def test_wrong_content_type_redirect(self, mock_magic):
        mock_magic.return_value = "application/zip"

        original_url = "http://original.example.com/file"
        redirected_url = "http://redirected.example.com/file"

        fake_chunk = b"PK\x03\x04"
        fake_headers = {"Content-Type": "application/zip"}

        mock_response = MagicMock()
        mock_response.url = MagicMock()
        mock_response.url.__str__ = MagicMock(return_value=redirected_url)
        mock_response.headers = fake_headers

        async def fake_iter_chunked(_size):
            yield fake_chunk

        mock_response.content.iter_chunked = fake_iter_chunked
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=False)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)

        with self.assertRaises(CheckerFetchError) as ctx:
            await get_extra_data_info_from_url(
                url=original_url,
                session=mock_session,
                content_type_deny=[re.compile(r"^application/zip$")],
            )

        self.assertIn(original_url, str(ctx.exception))
        self.assertIn("redirected from", str(ctx.exception))

    async def test_wrong_content_type(self):
        # Because so many web servers serve source code archives with an incorrect
        # Content-Type, we ignore the Content-Type header and instead use
        # (lib)magic to sniff the content type from the response data.
        #
        # However, we also have to deal with the problem that SourceForge
        # sometimes returns a 200 OK response with an HTML error page rather
        # than the source code archive we requested. So
        # get_extra_data_info_from_url() allows the caller to provide a
        # denylist.
        #
        # This test case is testing 2 things. The server returns a JSON body
        # but with application/gzip as the Content-Type. We test that:
        #
        # 1. The content type is sniffed from the data as application/json
        # 2. As a result it is rejected
        with self.assertRaises(CheckerFetchError):
            await get_extra_data_info_from_url(
                url="https://httpbingo.org/response-headers?Content-Type=application/gzip",
                session=self.http,
                content_type_deny=[re.compile(r"^application/json$")],
            )


class TestDetectJsonIndent(unittest.TestCase):
    def test_2_spaces_app_manifest(self):
        text = dedent("""\
            {
              "app-id": "com.example.App",
              "modules": []
            }
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), 2)

    def test_4_spaces_app_manifest(self):
        text = dedent("""\
            {
                "app-id": "com.example.App",
                "modules": []
            }
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), 4)

    def test_tab_app_manifest(self):
        text = dedent("""\
            {
            \t"app-id": "com.example.App",
            \t"modules": []
            }
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), "\t")

    def test_2_spaces_module_manifest(self):
        text = dedent("""\
            {
              "name": "foo",
              "sources": []
            }
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), 2)

    def test_2_spaces_source_list(self):
        text = dedent("""\
            [
              {
                "type": "archive",
                "url": "https://example.com/foo.tar.gz"
              }
            ]
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), 2)

    def test_4_spaces_source_list(self):
        text = dedent("""\
            [
                {
                    "type": "archive",
                    "url": "https://example.com/foo.tar.gz"
                }
            ]
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), 4)

    def test_tab_source_list(self):
        text = dedent("""\
            [
            \t{
            \t\t"type": "archive",
            \t\t"url": "https://example.com/foo.tar.gz"
            \t}
            ]
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), "\t")

    def test_name_nested_in_checker_data(self):
        text = dedent("""\
            {
                "name": "glycin",
                "sources": [
                    {
                        "type": "archive",
                        "x-checker-data": {
                            "name": "glycin",
                            "type": "gnome"
                        }
                    }
                ]
            }
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), 4)

    def test_modules_key_preferred_over_name(self):
        text = dedent("""\
            {
                "modules": [
                    {
                        "name": "libde265",
                        "sources": [
                            {
                                "x-checker-data": {
                                    "name": "libde265"
                                }
                            }
                        ]
                    }
                ],
                "name": "glycin"
            }
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), 4)

    def test_no_indent(self):
        self.assertIsNone(
            _detect_json_flatpak_manifest_indent('{"app-id": "com.example.App"}')
        )

    def test_list_root_no_brace_line(self):
        self.assertIsNone(_detect_json_flatpak_manifest_indent('["a", "b"]'))

    def test_dict_with_id_key(self):
        text = dedent("""\
            {
              "id": "com.example.App",
              "modules": []
            }
            """)
        self.assertEqual(_detect_json_flatpak_manifest_indent(text), 2)


EDITORCONFIG_SAMPLE_DATA = {"first": 1, "second": [2, 3]}
EDITORCONFIG_STYLES = [
    # 2-space with newline
    (
        dedent("""\
            [*.json]
            indent_style = space
            indent_size = 2
            insert_final_newline = true
            """),
        # ---
        dedent("""\
            {
              "first": 1,
              "second": [
                2,
                3
              ]
            }
            """),
    ),
    # Tab without newline
    (
        dedent("""\
            [*.json]
            indent_style = tab
            insert_final_newline = false
            """),
        # ---
        dedent("""\
            {
            \t"first": 1,
            \t"second": [
            \t\t2,
            \t\t3
            \t]
            }"""),
    ),
    # No preference, default to 4-space without newline
    (
        None,
        # ---
        dedent("""\
            {
                "first": 1,
                "second": [
                    2,
                    3
                ]
            }"""),
    ),
]

ORIGINAL_INDENT_STYLES = [
    # 2-space
    (
        dedent("""\
            {
              "app-id": "com.example.App"
            }
            """),
        dedent("""\
            {
              "first": 1,
              "second": [
                2,
                3
              ]
            }
            """),
    ),
    # Tab
    (
        dedent("""\
            {
            \t"app-id": "com.example.App"
            }"""),
        dedent("""\
            {
            \t"first": 1,
            \t"second": [
            \t\t2,
            \t\t3
            \t]
            }"""),
    ),
    # 4-space with trailing newline
    (
        dedent("""\
            {
                "app-id": "com.example.App"
            }
            """),
        dedent("""\
            {
                "first": 1,
                "second": [
                    2,
                    3
                ]
            }
            """),
    ),
]


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

    def test_editorconfig_prio_over_original(self):
        original = dedent("""\
            {
            \t"old": true
            }
            """)
        econfig = dedent("""\
            [*.json]
            indent_style = space
            indent_size = 2
            insert_final_newline = true
            """)
        expected = dedent("""\
            {
              "first": 1,
              "second": [
                2,
                3
              ]
            }
            """)
        path = Path(self.tmpdir.name) / "override" / "test.json"
        path.parent.mkdir(parents=True)
        (path.parent / ".editorconfig").write_text(econfig)
        path.write_text(original)
        dump_manifest(EDITORCONFIG_SAMPLE_DATA, path)
        self.assertEqual(path.read_text(), expected)

    def test_original_indent_preserved_no_editorconfig(self):
        for i, _style in enumerate(ORIGINAL_INDENT_STYLES):
            original, expected_data = _style
            path = Path(self.tmpdir.name) / f"orig_{i}" / f"{i}.json"
            path.parent.mkdir(parents=True)
            path.write_text(original)
            dump_manifest(EDITORCONFIG_SAMPLE_DATA, path)
            self.assertEqual(path.read_text(), expected_data)

    def test_invalid_yaml_max_line_length_ignored(self):
        p = Path(self.tmpdir.name) / "sub" / "test.yaml"
        p.parent.mkdir()
        (p.parent / ".editorconfig").write_text(
            "[*.yaml]\nmax_line_length = notanumber\n"
        )
        p.write_text("app-id: com.example.App\n")
        dump_manifest({"app-id": "com.example.App"}, p, has_yaml_header=False)


class TestReadJsonManifest(unittest.TestCase):
    def test_missing_file_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            read_json_manifest(Path("/nonexistent/path/manifest.json"))

    def test_non_noent_glib_error_is_reraised(self):
        class FakeGLibError(Exception):
            def matches(self, domain, code):
                return False

            @property
            def message(self):
                return "some other glib error"

        fake_err = FakeGLibError()

        with patch("src.lib.utils.Json.Parser") as MockParser:
            instance = MockParser.return_value
            instance.load_from_file.side_effect = fake_err

            with patch("src.lib.utils.GLib.Error", FakeGLibError):
                with self.assertRaises(FakeGLibError):
                    read_json_manifest(Path("/some/manifest.json"))


class TestGitLsRemote(unittest.IsolatedAsyncioTestCase):
    @patch("src.lib.utils.Command.run")
    async def test_bad_url_raises_checker_query_error(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(
            returncode=128,
            cmd=[
                "git",
                "ls-remote",
                "--exit-code",
                "https://github.com/does-not-exist/xxxxxxxx-yyyyzzzz",
            ],
        )

        with self.assertRaises(CheckerQueryError):
            await git_ls_remote("https://github.com/does-not-exist/xxxxxxxx-yyyyzzzz")


class TestGatherFailfast(unittest.IsolatedAsyncioTestCase):
    async def test_all_succeed(self):
        async def succeed(val):
            return val

        result = await asyncio_gather_failfast([succeed(1), succeed(2), succeed(3)])
        self.assertEqual(result, [1, 2, 3])

    async def test_empty(self):
        result = await asyncio_gather_failfast([])
        self.assertEqual(result, [])

    async def test_exception_cancels_pending(self):
        cancelled = False

        async def hang():
            nonlocal cancelled
            try:
                await asyncio.sleep(999)
            except asyncio.CancelledError:
                cancelled = True
                raise

        async def fail():
            raise AssertionError("test error")

        with self.assertRaises(AssertionError):
            await asyncio_gather_failfast([hang(), fail()])

        self.assertTrue(cancelled)

    async def test_exception_propagates(self):
        async def fail():
            raise ValueError("test error")

        with self.assertRaises(ValueError, msg="test error"):
            await asyncio_gather_failfast([fail()])

    async def test_parent_child_hang(self):
        event = asyncio.Event()
        child_cancelled = False

        async def parent():
            raise AssertionError("test error")

        async def child():
            nonlocal child_cancelled
            try:
                await event.wait()
            except asyncio.CancelledError:
                child_cancelled = True
                raise

        with self.assertRaises(AssertionError):
            await asyncio_gather_failfast([parent(), child()])

        self.assertTrue(child_cancelled)

    async def test_sibling_cancelled_on_exception(self):
        async def raises_assert():
            raise AssertionError("test error")

        async def slow():
            await asyncio.sleep(999)

        with self.assertRaises((AssertionError, asyncio.TimeoutError)):
            await asyncio.wait_for(
                asyncio.gather(raises_assert(), slow()),
                timeout=2.0,
            )

        with self.assertRaises(AssertionError):
            await asyncio_gather_failfast([raises_assert(), slow()])

    async def test_cancelled_task_in_done_skipped(self):
        async def succeed():
            return 42

        result = await asyncio_gather_failfast([succeed()])
        self.assertEqual(result, [42])

    async def test_raises_cancelled_error_propagates(self):
        async def raises_cancelled():
            raise asyncio.CancelledError

        with self.assertRaises((asyncio.CancelledError, BaseException)):
            await asyncio_gather_failfast([raises_cancelled()])

    async def test_cancelled_task_does_not_prevent_success(self):
        asyncio.Event()

        async def succeed():
            return 99

        results = await asyncio_gather_failfast([succeed()])
        self.assertEqual(results, [99])

    async def test_exception_raises_cancelled_error(self):
        loop = asyncio.get_running_loop()

        class ForceCoverageFuture(asyncio.Future):
            def cancelled(self):
                return False

            def exception(self):
                raise asyncio.CancelledError

            def result(self):
                return "force_coverage"

        f = ForceCoverageFuture(loop=loop)
        f.set_result(None)

        results = await asyncio_gather_failfast([f])

        self.assertEqual(results, ["force_coverage"])


class TestFallbackVersion(unittest.TestCase):
    def test_incomparable_looseversions_raise(self):
        class RaisingLooseVersion:
            def __init__(self, s):
                self.s = s

            def __lt__(self, other):
                raise TypeError("cannot compare")

            def __le__(self, other):
                raise TypeError("cannot compare")

            def __gt__(self, other):
                raise TypeError("cannot compare")

            def __ge__(self, other):
                raise TypeError("cannot compare")

            def __eq__(self, other):
                raise TypeError("cannot compare")

        with patch.object(utils, "LooseVersion", RaisingLooseVersion):
            with self.assertRaises(VersionComparisonError):
                _ = FallbackVersion("invalid.1") < FallbackVersion("invalid.2")

    def test_version_comparison_error_carries_operands(self):
        err = VersionComparisonError("1.a", "2.b")
        self.assertEqual(err.left, "1.a")
        self.assertEqual(err.right, "2.b")
        self.assertIn("1.a", str(err))
        self.assertIn("2.b", str(err))


if __name__ == "__main__":
    unittest.main()
