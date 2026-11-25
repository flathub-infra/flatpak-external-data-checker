import asyncio
import logging
from collections.abc import Sequence
from contextvars import ContextVar, Token
from types import SimpleNamespace
from typing import TypedDict, cast

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.connector import Connection
from aiohttp.tracing import (
    Trace,
    TraceRequestEndParams,
    TraceRequestExceptionParams,
    TraceRequestStartParams,
)

log = logging.getLogger(__name__)


class _RequestTrace(SimpleNamespace):
    token: Token | None = None
    candidates: dict[tuple[str, int], list[str]]


_request_trace: ContextVar[_RequestTrace | None] = ContextVar(
    "request_trace", default=None
)


def _connection_candidate_trace() -> aiohttp.TraceConfig:
    trace = aiohttp.TraceConfig(trace_config_ctx_factory=_RequestTrace)

    def reset(context: _RequestTrace) -> bool:
        if context.token is None:
            return False
        _request_trace.reset(context.token)
        context.token = None
        return True

    async def start(
        session: aiohttp.ClientSession,
        context: SimpleNamespace,
        params: TraceRequestStartParams,
    ) -> None:
        request = cast(_RequestTrace, context)
        request.candidates = {}
        request.token = _request_trace.set(request)

    async def end(
        session: aiohttp.ClientSession,
        context: SimpleNamespace,
        params: TraceRequestEndParams,
    ) -> None:
        reset(cast(_RequestTrace, context))

    async def failed(
        session: aiohttp.ClientSession,
        context: SimpleNamespace,
        params: TraceRequestExceptionParams,
    ) -> None:
        request = cast(_RequestTrace, context)
        if not reset(request):
            return
        # Candidates survive only failed connection setup, which includes
        # reading a proxy's CONNECT response. At the request boundary aiohttp
        # has translated its deadline cancellation into a timeout.
        if isinstance(params.exception, (asyncio.TimeoutError, aiohttp.ClientError)):
            for (host, port), addresses in request.candidates.items():
                log.debug(
                    "Connection failed for %s on port %d; address candidates "
                    "(not necessarily attempted): %s",
                    host,
                    port,
                    ", ".join(addresses),
                )

    trace.on_request_start.append(start)
    trace.on_request_end.append(end)
    trace.on_request_exception.append(failed)
    return trace


class _ConnectionCandidateConnector(aiohttp.TCPConnector):
    """Collect address candidates for connection failure diagnostics."""

    async def connect(
        self,
        req: aiohttp.ClientRequest,
        traces: list[Trace],
        timeout: aiohttp.ClientTimeout,
    ) -> Connection:
        context = _request_trace.get()
        if context is not None:
            context.candidates.clear()
        connection = await super().connect(req, traces, timeout)
        if context is not None:
            context.candidates.clear()
        return connection

    async def _resolve_host(
        self, host: str, port: int, traces: Sequence[Trace] | None = None
    ) -> list[ResolveResult]:
        # Capture the connector's results, including cache hits, without
        # replacing its resolver or doing a second lookup on failure.
        records = await super()._resolve_host(host, port, traces)
        context = _request_trace.get()
        if context is not None and records:
            context.candidates[host, port] = list(
                dict.fromkeys(record["host"] for record in records)
            )
        return records


class _ConnectionDiagnosticsOptions(TypedDict):
    connector: aiohttp.TCPConnector
    trace_configs: list[aiohttp.TraceConfig]


def connection_diagnostics_options(
    *, resolver: AbstractResolver | None = None
) -> _ConnectionDiagnosticsOptions:
    """Return the paired connector and trace for an aiohttp session."""
    return {
        "connector": _ConnectionCandidateConnector(resolver=resolver),
        "trace_configs": [_connection_candidate_trace()],
    }
