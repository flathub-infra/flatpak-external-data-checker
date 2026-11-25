import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, patch

import aiohttp
from aiohttp.abc import AbstractResolver
from aiohttp.helpers import TimeoutHandle

from src.lib.http import connection_diagnostics_options

from .support import LocalHTTPTestCase


class TestConnectionDiagnostics(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        resolver = AsyncMock(spec=AbstractResolver)
        resolver.resolve.return_value = [
            {
                "hostname": "example.invalid",
                "host": address,
                "port": 80,
                "family": family,
                "proto": socket.IPPROTO_TCP,
                "flags": socket.AI_NUMERICHOST,
            }
            for address, family in [
                ("2001:db8::1", socket.AF_INET6),
                ("192.0.2.1", socket.AF_INET),
            ]
        ]
        self.resolver = resolver

    def _session(self, timeout=None):
        return aiohttp.ClientSession(
            timeout=timeout,
            **connection_diagnostics_options(resolver=self.resolver),
        )

    async def test_logs_dual_stack_candidates_on_connect_timeout_with_cached_dns(self):
        async with self._session() as session:
            with patch(
                "aiohappyeyeballs.start_connection", side_effect=asyncio.TimeoutError
            ):
                for _ in range(2):
                    with (
                        self.assertLogs("src.lib.http", level="DEBUG") as logs,
                        self.assertRaises(aiohttp.ConnectionTimeoutError),
                    ):
                        await session.get("http://example.invalid/")
                    self.assertEqual(len(logs.records), 1)
                    self.assertIn(
                        "example.invalid on port 80; address candidates "
                        "(not necessarily attempted):",
                        logs.records[0].getMessage(),
                    )
                    self.assertIn("192.0.2.1", logs.records[0].getMessage())
                    self.assertIn("2001:db8::1", logs.records[0].getMessage())

        self.resolver.resolve.assert_awaited_once()

    async def test_no_candidates_logged_at_default_info_level(self):
        async with self._session() as session:
            with (
                patch(
                    "aiohappyeyeballs.start_connection",
                    side_effect=asyncio.TimeoutError,
                ),
                self.assertNoLogs("src.lib.http", level="INFO"),
                self.assertRaises(aiohttp.ConnectionTimeoutError),
            ):
                await session.get("http://example.invalid/")

    async def test_logs_candidates_when_total_timeout_interrupts_connection(self):
        deadline: TimeoutHandle | None = None

        def capture_deadline(handle: TimeoutHandle) -> None:
            nonlocal deadline
            deadline = handle

        async def expire_deadline(*args, **kwargs):
            # Fire aiohttp's real timer callback only after DNS has completed.
            assert deadline is not None
            deadline()
            await asyncio.sleep(0)

        async with self._session(aiohttp.ClientTimeout(total=60)) as session:
            with (
                patch.object(
                    TimeoutHandle, "start", autospec=True, side_effect=capture_deadline
                ),
                patch(
                    "aiohappyeyeballs.start_connection", side_effect=expire_deadline
                ) as connect,
                self.assertLogs("src.lib.http", level="DEBUG") as logs,
                self.assertRaises(asyncio.TimeoutError) as error,
            ):
                await session.get("http://example.invalid/")
        connect.assert_awaited_once()
        self.assertNotIsInstance(error.exception, aiohttp.ConnectionTimeoutError)
        self.assertEqual(len(logs.records), 1)
        self.assertIn("192.0.2.1", logs.records[0].getMessage())
        self.assertIn("2001:db8::1", logs.records[0].getMessage())

    async def test_no_candidates_logged_when_caller_cancels_connection(self):
        async def cancel_connection(*args, **kwargs):
            task = asyncio.current_task()
            assert task is not None
            task.cancel()
            await asyncio.sleep(0)

        async with self._session() as session:
            with (
                patch(
                    "aiohappyeyeballs.start_connection", side_effect=cancel_connection
                ),
                self.assertNoLogs("src.lib.http", level="DEBUG"),
                self.assertRaises(asyncio.CancelledError),
            ):
                await asyncio.create_task(session.get("http://example.invalid/"))

    async def test_logs_proxy_address_when_connect_tunnel_disconnects(self):
        await self._assert_proxy_failure_logs_address(
            b"", aiohttp.ServerDisconnectedError
        )

    async def test_logs_proxy_address_when_connect_tunnel_is_rejected(self):
        await self._assert_proxy_failure_logs_address(
            b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n",
            aiohttp.ClientHttpProxyError,
        )

    async def test_logs_proxy_address_when_connect_response_is_invalid(self):
        await self._assert_proxy_failure_logs_address(
            b"INVALID STATUS\r\n\r\n", aiohttp.ClientResponseError
        )

    async def _assert_proxy_failure_logs_address(self, response, error):
        async def reject_tunnel(reader, writer):
            try:
                await reader.readuntil(b"\r\n\r\n")
                if response:
                    writer.write(response)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        # Use a raw TCP peer to control what happens during the CONNECT handshake.
        server = await asyncio.start_server(reject_tunnel, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            async with self._session(aiohttp.ClientTimeout(total=5)) as session:
                with (
                    self.assertLogs("src.lib.http", level="DEBUG") as logs,
                    self.assertRaises(error),
                ):
                    await session.get(
                        "https://example.invalid/", proxy=f"http://127.0.0.1:{port}"
                    )
        self.assertIn(
            f"127.0.0.1 on port {port}; address candidates "
            "(not necessarily attempted): 127.0.0.1",
            logs.records[0].getMessage(),
        )

    async def test_no_candidates_logged_when_dns_fails(self):
        self.resolver.resolve.side_effect = OSError("DNS lookup failed")
        async with self._session() as session:
            with (
                self.assertNoLogs("src.lib.http", level="DEBUG"),
                self.assertRaises(aiohttp.ClientConnectorDNSError),
            ):
                await session.get("http://example.invalid/")


class TestResponseDiagnostics(LocalHTTPTestCase):
    async def test_no_candidates_logged_for_http_error_after_connection_setup(self):
        url = self.server.make_url("/missing")
        async with aiohttp.ClientSession(
            raise_for_status=True,
            timeout=aiohttp.ClientTimeout(total=5),
            **connection_diagnostics_options(),
        ) as session:
            with (
                self.assertNoLogs("src.lib.http", level="DEBUG"),
                self.assertRaises(aiohttp.ClientResponseError) as error,
            ):
                await session.get(url)
        self.assertEqual(error.exception.status, 404)
