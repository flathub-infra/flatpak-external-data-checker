import asyncio
import gzip
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import aiohttp

from src.checkers.anityachecker import AnityaChecker
from src.checkers.gitchecker import GitChecker
from src.lib.externaldata import ExternalData, ExternalGitRepo
from src.manifest import ManifestChecker
from tests import _offline_network_guard
from tests import support as test_support
from tests.support import LocalHTTPTestCase


class TestOfflineTransports(LocalHTTPTestCase):
    async def test_manifest_relative_redirect_and_truncated_download(self):
        body = b"30-50 feral hogs!\n"
        download = self.serve(body, content_type="text/plain")
        self.responses["/latest"] = {
            "status": 302,
            "headers": {"Location": download.rsplit("/", 1)[1]},
        }

        async def incomplete_response(reader, writer):
            try:
                await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=5)
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n"
                    b"Connection: close\r\n\r\nshort body"
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(incomplete_response, "127.0.0.1", 0)
        self.addAsyncCleanup(server.wait_closed)
        self.addCleanup(server.close)
        with tempfile.TemporaryDirectory() as directory:
            port = server.sockets[0].getsockname()[1]
            sources = [
                {
                    "type": "extra-data",
                    "filename": filename,
                    "url": url,
                    "sha256": "0" * 64,
                    "size": 0,
                }
                for filename, url in (
                    ("truncated.txt", f"http://127.0.0.1:{port}/truncated"),
                    ("redirect.txt", str(self.server.make_url("/old"))),
                )
            ]
            sources[1]["x-checker-data"] = {
                "type": "rotating-url",
                "url": str(self.server.make_url("/latest")),
            }
            path = Path(directory) / "sources.json"
            path.write_text(json.dumps(sources))
            checker = ManifestChecker(str(path))
            truncated, redirected = await asyncio.wait_for(checker.check(), timeout=5)
        self.assertTrue(truncated.checked.is_set())
        self.assertTrue(truncated.state & truncated.State.BROKEN)
        self.assertIsNone(truncated.new_version)
        self.assertEqual(len(checker.get_errors()), 1)
        self.assertIsInstance(
            checker.get_errors()[0].__cause__, aiohttp.ClientPayloadError
        )
        self.assertTrue(redirected.checked.is_set())
        self.assertEqual(redirected.new_version.url, download)
        self.assertEqual(redirected.new_version.size, len(body))
        self.assertEqual(
            redirected.new_version.checksum.sha256, hashlib.sha256(body).hexdigest()
        )

    async def test_anitya_api_and_download(self):
        body = gzip.compress(b"deterministic release archive", mtime=0)
        download = self.serve(body)
        self.responses["/api/v2/versions/"] = {
            "text": json.dumps(
                {
                    "latest_version": "2.0",
                    "stable_versions": ["2.0", "1.0"],
                    "latest_version_created_on": "2024-01-02T03:04:05Z",
                }
            ),
            "content_type": "application/json",
        }
        data = ExternalData.from_source(
            "test.json",
            {
                "type": "archive",
                "url": str(self.server.make_url("/old-1.0.tar.gz")),
                "sha256": "0" * 64,
                "x-checker-data": {
                    "type": "anitya",
                    "baseurl": str(self.server.make_url("/")),
                    "project-id": 42,
                    "url-template": download,
                },
            },
        )
        checker = AnityaChecker(self.http)
        await checker.validate_checker_data(data)
        await asyncio.wait_for(checker.check(data), timeout=5)
        self.assertEqual(
            self.requests,
            [("GET", "/api/v2/versions/?project_id=42"), ("GET", "/response-0")],
        )
        self.assertEqual(data.new_version.url, download)
        self.assertEqual(data.new_version.version, "2.0")
        self.assertEqual(data.new_version.size, len(body))
        self.assertEqual(
            data.new_version.checksum.sha256, hashlib.sha256(body).hexdigest()
        )
        self.assertEqual(
            data.new_version.timestamp.isoformat(), "2024-01-02T03:04:05+00:00"
        )
        data.update()
        self.assertEqual(data.source["url"], download)
        self.assertEqual(data.source["sha256"], hashlib.sha256(body).hexdigest())

    async def test_local_git_tags_and_update(self):
        with tempfile.TemporaryDirectory() as directory:

            def git(*args):
                return subprocess.check_output(
                    ["git", "-C", directory, *args],
                    env=os.environ
                    | {
                        "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_CONFIG_GLOBAL": os.devnull,
                        "GIT_AUTHOR_NAME": "Test",
                        "GIT_AUTHOR_EMAIL": "test@localhost",
                        "GIT_COMMITTER_NAME": "Test",
                        "GIT_COMMITTER_EMAIL": "test@localhost",
                    },
                    text=True,
                    timeout=5,
                ).strip()

            git("init", "--initial-branch=main")
            git("commit", "--allow-empty", "-m", "old")
            old = git("rev-parse", "HEAD")
            git("tag", "v1.0.0")
            git("commit", "--allow-empty", "-m", "new")
            new = git("rev-parse", "HEAD")
            git("tag", "-a", "v2.0.0", "-m", "annotated release")
            git("tag", "v3.0.0")
            data = ExternalGitRepo.from_source_impl(
                "test.json",
                {
                    "type": "git",
                    "url": Path(directory).as_uri(),
                    "tag": "v1.0.0",
                    "commit": old,
                    "x-checker-data": {
                        "type": "git",
                        "version-scheme": "semantic",
                        "versions": {"<": "3.0.0"},
                    },
                },
            )
            checker = GitChecker(self.http)
            await checker.validate_checker_data(data)
            # Production only exposes system paths in its remote-Git sandbox;
            # this test deliberately uses a temporary local repository instead.
            with (
                mock.patch("src.lib.utils.check_bwrap", return_value=False),
                mock.patch("src.lib.utils.os.getcwd", return_value=directory),
            ):
                await asyncio.wait_for(checker.check(data), timeout=5)
            self.assertEqual(data.new_version.tag, "v2.0.0")
            self.assertEqual(data.new_version.version, "2.0.0")
            self.assertEqual(data.new_version.commit, new)
            self.assertNotEqual(new, old)
            data.update()
            self.assertEqual(data.source["commit"], new)
            self.assertEqual(data.source["tag"], "v2.0.0")

    def test_network_guard_rejects_external_transports(self):
        sock = mock.Mock(family=socket.AF_INET)
        for event, args in (
            ("socket.getaddrinfo", ("release-monitoring.org", 443)),
            ("socket.getaddrinfo", (b"release-monitoring.org", 443)),
            ("socket.getaddrinfo", (b"\xff", 443)),
            ("socket.connect", (sock, ("203.0.113.1", 443))),
            ("socket.sendto", (sock, b"dns", ("203.0.113.1", 53))),
        ):
            with self.subTest(event=event):
                with self.assertRaisesRegex(AssertionError, "External network access"):
                    _offline_network_guard(event, args)
        for host in (
            None,
            "localhost",
            "LOCALHOST",
            b"localhost",
            "127.0.0.1",
            b"127.0.0.1",
            "::1",
        ):
            _offline_network_guard("socket.getaddrinfo", (host, 80))


class TestUpstreamPolicy(unittest.IsolatedAsyncioTestCase):
    def test_default_import_installs_network_guard(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import tests, socket; "
                "socket.getaddrinfo('release-monitoring.org', 443)",
            ],
            env=os.environ | {"FEDC_TEST_UPSTREAM": "0"},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AssertionError: External network access", result.stderr)

    async def test_upstream_requires_explicit_opt_in(self):
        probe = mock.AsyncMock()
        with mock.patch.object(test_support, "UPSTREAM_TESTS", False):
            marked = test_support.upstream_test(probe)
        self.assertTrue(marked.__unittest_skip__)
        probe.assert_not_awaited()

    async def test_opted_in_test_has_time_budget(self):
        probe = mock.AsyncMock()
        with mock.patch.object(test_support, "UPSTREAM_TESTS", True):
            marked = test_support.upstream_test(probe)
        with mock.patch(
            "tests.support.asyncio.wait_for", wraps=asyncio.wait_for
        ) as wait:
            await marked(self)
        probe.assert_awaited_once_with(self)
        self.assertEqual(wait.call_args.kwargs["timeout"], 60)
