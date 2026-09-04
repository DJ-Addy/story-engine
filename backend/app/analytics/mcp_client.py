"""Transport: ClickHouse reached through the official ``mcp-clickhouse`` server.

Story Engine never opens a ClickHouse connection itself. Every statement — the
migration DDL, the batched event ``INSERT``s and every dashboard ``SELECT`` —
is sent as an MCP ``call_tool`` request to ClickHouse's own MCP server
(https://github.com/ClickHouse/mcp-clickhouse), which owns the ``clickhouse-connect``
driver, the credentials and the read-only enforcement. Two transports are
supported, both from the MCP Python SDK:

* ``stdio`` (default) — the server is launched as a child process and spoken to
  over its stdin/stdout, exactly as ClickHouse's own agent-library docs show.
  Cluster credentials are forwarded in the child's environment under
  ClickHouse's documented ``CLICKHOUSE_*`` names.
* ``http`` — a streamable-HTTP MCP endpoint that is already running elsewhere
  (``CLICKHOUSE_MCP_SERVER_TRANSPORT=http``, or a managed/remote MCP server),
  with an optional bearer token.

**Why a pump task.** The MCP SDK's client context managers are anyio task
groups: entering and exiting them from different tasks raises cancel-scope
errors, which is precisely what happens if a session opened inside one HTTP
request is closed by a shutdown handler. So one dedicated task owns the whole
session lifecycle and serves requests off a queue. That also serialises
statements onto a single session, which is what we want — the writer batches
anyway, and ClickHouse would rather have one big insert than many small ones.

A dead session is not retried in place: the pump exits, and the next call
starts a fresh one. Reconnect is therefore a consequence of the design rather
than a retry loop that has to be tuned.

The ``mcp`` SDK is imported lazily inside the session factories so this module
imports — and the whole test suite collects — with no MCP package installed and
no network available.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.analytics.settings import AnalyticsSettings

logger = logging.getLogger(__name__)


class ClickHouseUnavailable(RuntimeError):
    """The cluster (or the MCP server in front of it) could not serve a statement.

    Every analytics call site treats this as non-fatal: the API keeps serving
    with a gap in the data rather than failing a render because a demo cluster
    blinked.
    """


class QueryResult(BaseModel):
    """A columnar result set as ``run_query`` returns it."""

    columns: list[str] = Field(default_factory=list)
    rows: list[list[Any]] = Field(default_factory=list)

    def dicts(self) -> list[dict[str, Any]]:
        return [dict(zip(self.columns, row, strict=False)) for row in self.rows]


@runtime_checkable
class QueryRunner(Protocol):
    """The seam every layer above this one depends on.

    Narrow by design: one method to run SQL, one to shut down. Tests substitute
    an in-memory double implementing exactly this, which is how the suite covers
    the real code paths without a cluster or a socket.
    """

    async def run_query(self, sql: str) -> QueryResult: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class MCPSession(Protocol):
    """The slice of ``mcp.ClientSession`` this module uses."""

    async def initialize(self) -> Any: ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


SessionFactory = Callable[[], AbstractAsyncContextManager[MCPSession]]


# --------------------------------------------------------------------------- #
# Result parsing
# --------------------------------------------------------------------------- #
def _text_payload(result: Any) -> Any:
    """Pull the tool's payload out of an MCP ``CallToolResult``.

    ``mcp-clickhouse`` returns a JSON *string*, which FastMCP delivers both as a
    ``TextContent`` block and (wrapped as ``{"result": ...}``) in
    ``structuredContent``. Both shapes are accepted, plus a plain dict, because
    the exact wrapping has changed across SDK releases and pinning our parser to
    one of them would break on an upgrade of a dependency we do not control.
    """
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        payload = structured.get("result", structured)
        if payload is not None:
            return payload
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text is not None:
            return text
    if isinstance(result, (str, dict, list)):
        return result
    return None


def parse_tool_result(result: Any) -> QueryResult:
    """Normalise whatever the MCP tool returned into a :class:`QueryResult`.

    Raises :class:`ClickHouseUnavailable` when the tool reported an error, so
    the failure reaches callers as the same class as a transport failure — from
    the caller's point of view "ClickHouse did not answer" is one condition.
    """
    if getattr(result, "isError", False):
        raise ClickHouseUnavailable(f"clickhouse mcp tool error: {_text_payload(result)}")

    payload = _text_payload(result)
    if isinstance(payload, str):
        stripped = payload.strip()
        if not stripped:
            return QueryResult()
        try:
            payload = json.loads(stripped)
        except ValueError:
            # DDL and INSERT return a human-readable acknowledgement, not JSON.
            return QueryResult()

    if payload is None:
        return QueryResult()
    if isinstance(payload, dict):
        if "error" in payload and "rows" not in payload:
            raise ClickHouseUnavailable(f"clickhouse query failed: {payload['error']}")
        columns = [str(name) for name in payload.get("columns", [])]
        rows = [list(row) for row in payload.get("rows", [])]
        return QueryResult(columns=columns, rows=rows)
    if isinstance(payload, list):
        # Row-of-dicts shape (older servers): derive columns from the first row.
        if payload and isinstance(payload[0], dict):
            columns = list(payload[0])
            return QueryResult(
                columns=columns,
                rows=[[row.get(name) for name in columns] for row in payload],
            )
        return QueryResult()
    return QueryResult()


# --------------------------------------------------------------------------- #
# Session factories (the only place the mcp SDK is touched)
# --------------------------------------------------------------------------- #
@asynccontextmanager
async def _stdio_session(settings: AnalyticsSettings) -> AsyncIterator[MCPSession]:
    """Launch ``mcp-clickhouse`` as a child process and speak MCP over its pipes."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    try:
        from mcp.client.stdio import get_default_environment

        base_env = get_default_environment()
    except ImportError:  # pragma: no cover - SDK layout change
        base_env = dict(os.environ)

    params = StdioServerParameters(
        command=settings.command,
        args=list(settings.args),
        env={**base_env, **settings.server_env},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            yield session


@asynccontextmanager
async def _http_session(settings: AnalyticsSettings) -> AsyncIterator[MCPSession]:
    """Attach to an already-running MCP endpoint over streamable HTTP."""
    from mcp import ClientSession
    from mcp.client.streamable_http import (
        create_mcp_http_client,
        streamable_http_client,
    )

    if not settings.url:
        raise ClickHouseUnavailable(
            "http transport selected but STORY_ENGINE_MCP_URL is unset"
        )
    headers = (
        {"Authorization": f"Bearer {settings.auth_token}"} if settings.auth_token else None
    )
    # mcp 2.x: the client no longer takes headers or yields a third element, so
    # auth rides on an httpx client built by the SDK's own helper.
    async with create_mcp_http_client(headers) as http_client:
        async with streamable_http_client(
            settings.url, http_client=http_client
        ) as (read, write):
            async with ClientSession(read, write) as session:
                yield session


def default_session_factory(settings: AnalyticsSettings) -> SessionFactory:
    if settings.transport == "http":
        return lambda: _http_session(settings)
    return lambda: _stdio_session(settings)


# --------------------------------------------------------------------------- #
# The runner
# --------------------------------------------------------------------------- #
@dataclass
class _Request:
    sql: str
    future: asyncio.Future = field(repr=False)


class ClickHouseMCPRunner:
    """Runs SQL against ClickHouse through one long-lived MCP session."""

    def __init__(
        self,
        settings: AnalyticsSettings,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self._settings = settings
        self._factory = session_factory or default_session_factory(settings)
        self._queue: asyncio.Queue[_Request | None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    # -- public surface ----------------------------------------------------
    async def run_query(self, sql: str) -> QueryResult:
        """Send one statement and wait for its result.

        Raises :class:`ClickHouseUnavailable` for anything that goes wrong —
        session start-up, tool error, transport fault or timeout — so callers
        have a single failure type to be non-fatal about.
        """
        loop = asyncio.get_running_loop()
        request = _Request(sql=sql, future=loop.create_future())
        async with self._lock:
            queue = await self._ensure_pump()
            queue.put_nowait(request)
        try:
            raw = await asyncio.wait_for(request.future, timeout=self._settings.timeout_s)
        except asyncio.TimeoutError as exc:
            # A timed-out statement leaves the session mid-request; drop it so
            # the next call gets a clean one rather than a desynchronised pipe.
            await self.aclose()
            raise ClickHouseUnavailable(
                f"clickhouse mcp query timed out after {self._settings.timeout_s}s"
            ) from exc
        return parse_tool_result(raw)

    async def aclose(self) -> None:
        """Stop the session task, waiting briefly for a clean shutdown."""
        async with self._lock:
            task, queue = self._task, self._queue
            self._task = None
            self._queue = None
        if task is None:
            return
        if queue is not None:
            queue.put_nowait(None)  # sentinel: finish in-flight work, then stop
        done, _pending = await asyncio.wait({task}, timeout=5.0)
        if not done:
            task.cancel()
        # gather(return_exceptions=True) retrieves whatever the task ended with,
        # so a failed or cancelled session never surfaces as an unretrieved
        # exception warning during application shutdown.
        await asyncio.gather(task, return_exceptions=True)

    # -- internals ---------------------------------------------------------
    async def _ensure_pump(self) -> asyncio.Queue[_Request | None]:
        """Start the session task if none is alive; return its request queue."""
        if self._task is not None and not self._task.done() and self._queue is not None:
            return self._queue
        queue: asyncio.Queue[_Request | None] = asyncio.Queue()
        ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._queue = queue
        self._task = asyncio.create_task(
            self._pump(queue, ready), name="clickhouse-mcp-session"
        )
        await ready  # raises ClickHouseUnavailable if the session never opened
        return queue

    async def _pump(
        self, queue: asyncio.Queue[_Request | None], ready: asyncio.Future[None]
    ) -> None:
        """Own one MCP session for its whole lifetime and serve queued statements."""
        failure: ClickHouseUnavailable | None = None
        try:
            async with self._factory() as session:
                await session.initialize()
                if not ready.done():
                    ready.set_result(None)
                while True:
                    request = await queue.get()
                    if request is None:
                        return
                    if request.future.done():
                        continue  # caller already timed out or was cancelled
                    try:
                        raw = await session.call_tool(
                            self._settings.tool_name, {"query": request.sql}
                        )
                    except Exception as exc:
                        failure = ClickHouseUnavailable(
                            f"clickhouse mcp call failed: {exc}"
                        )
                        _settle_exception(request.future, failure)
                        return  # session is suspect; the next call opens a new one
                    if not request.future.done():
                        request.future.set_result(raw)
        except asyncio.CancelledError:
            failure = ClickHouseUnavailable("clickhouse mcp session cancelled")
            raise
        except Exception as exc:
            failure = ClickHouseUnavailable(f"clickhouse mcp session failed: {exc}")
            logger.warning("clickhouse mcp session failed: %s", exc)
        finally:
            ended = failure or ClickHouseUnavailable("clickhouse mcp session closed")
            if not ready.done():
                ready.set_exception(ended)
            _drain(queue, ended)


def _settle_exception(future: asyncio.Future, error: BaseException) -> None:
    if not future.done():
        future.set_exception(error)


def _drain(queue: asyncio.Queue[_Request | None], error: BaseException) -> None:
    """Fail every statement still waiting when the session ends."""
    while True:
        try:
            pending = queue.get_nowait()
        except asyncio.QueueEmpty:
            return
        if pending is not None:
            _settle_exception(pending.future, error)
