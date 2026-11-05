"""Small, shared fixtures for deterministic HTTP tests and upstream smoke tests."""

import asyncio
import os
import unittest
from functools import wraps

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer

UPSTREAM_TESTS = os.environ.get("FEDC_TEST_UPSTREAM") == "1"

TAG_PATTERN_CASES = (
    (r"^[0-9]+(?:\.[0-9]+)+$", "1.2.3", "1.2.3"),
    (r"^v[0-9]+(?:\.[0-9]+)+$", "v1.2.3", "v1.2.3"),
    (r"^v?([0-9]+(?:\.[0-9]+)+)$", "v1.2.3", "1.2.3"),
    (r"^v?([0-9]+(?:\.[0-9]+)+)$", "1.2.3", "1.2.3"),
    (r"^release-([0-9]+(?:\.[0-9]+)+)$", "release-1.2.3", "1.2.3"),
    (r"^[0-9]+\.[0-9]+\.[0-9]+-rc\.[0-9]+$", "1.2.3-rc.1", "1.2.3-rc.1"),
    (r"^(?P<version>[0-9.]+)$", "1.2.3", "1.2.3"),
)


def upstream_test(test):
    """Retain live coverage, explicitly opted in and bounded per test."""

    @wraps(test)
    async def bounded(self):
        await asyncio.wait_for(test(self), timeout=60)

    return unittest.skipUnless(UPSTREAM_TESTS, "set FEDC_TEST_UPSTREAM=1")(bounded)


class LocalHTTPTestCase(unittest.IsolatedAsyncioTestCase):
    """Serve exact response bytes on loopback, with bounded client requests."""

    async def asyncSetUp(self):
        self.responses = {}
        self.requests = []
        app = web.Application()
        app.router.add_get("/{path:.*}", self._respond)
        self.server = TestServer(app)
        await self.server.start_server()
        self.addAsyncCleanup(self.server.close)
        self.http = aiohttp.ClientSession(
            raise_for_status=True, timeout=aiohttp.ClientTimeout(total=5)
        )
        self.addAsyncCleanup(self.http.close)

    async def _respond(self, request):
        self.requests.append((request.method, request.path_qs))
        return web.Response(**self.responses.get(request.path, {"status": 404}))

    def serve(
        self,
        body,
        *,
        content_type="application/octet-stream",
        charset=None,
        headers=None,
    ):
        path = f"/response-{len(self.responses)}"
        self.responses[path] = {
            "body": body,
            "content_type": content_type,
            "charset": charset,
            "headers": headers,
        }
        return str(self.server.make_url(path))
