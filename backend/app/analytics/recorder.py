"""Non-fatal, buffered event recording — the seam the API routers call.

Two guarantees the routers depend on and neither of which the client offers:

1. **``record()`` never raises and never awaits.** A judge run or a render must
   not slow down, and must certainly not fail, because a cluster is having a
   bad minute. Events land in a bounded in-memory buffer and a background task
   drains them.
2. **Writes are batched.** ClickHouse merges parts on write; a row-per-request
   insert pattern is the canonical way to make a cluster miserable. Buffering
   for a couple of seconds turns a burst of judge rows into one statement.

The buffer is bounded and drops the *oldest* events when it overflows: the
freshest telemetry is the useful telemetry during a demo, and unbounded growth
behind an unreachable cluster would be a memory leak dressed up as durability.
Drops are counted and reported through the status endpoint rather than hidden.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Iterable

from app.analytics.client import AnalyticsClient, try_insert
from app.analytics.events import AnalyticsEvent
from app.analytics.mcp_client import (
    ClickHouseMCPRunner,
    ClickHouseUnavailable,
    QueryRunner,
)
from app.analytics.settings import AnalyticsSettings

logger = logging.getLogger(__name__)


class EventRecorder:
    """Buffers analytics events and drains them to ClickHouse in the background."""

    def __init__(
        self,
        client: AnalyticsClient | None,
        *,
        flush_interval_s: float = 2.0,
        batch_size: int = 200,
        buffer_max: int = 5000,
    ) -> None:
        self._client = client
        self._flush_interval_s = flush_interval_s
        self._batch_size = batch_size
        self._buffer: deque[AnalyticsEvent] = deque(maxlen=max(1, buffer_max))
        self._dropped = 0
        self._written = 0
        self._failed = 0
        self._drainer: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._closed = False

    # -- properties --------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return self._client is not None

    @property
    def client(self) -> AnalyticsClient | None:
        return self._client

    def stats(self) -> dict[str, int | bool]:
        return {
            "enabled": self.enabled,
            "buffered": len(self._buffer),
            "written": self._written,
            "dropped": self._dropped,
            "failed": self._failed,
        }

    # -- writing -----------------------------------------------------------
    def record(self, events: Iterable[AnalyticsEvent]) -> int:
        """Buffer events for the background drain. Never raises, never blocks.

        Returns how many were accepted (0 when analytics is disabled), so a
        caller that wants to assert on emission can, without any call site
        having to care whether a cluster exists.
        """
        if self._client is None or self._closed:
            return 0
        accepted = 0
        for event in events:
            if len(self._buffer) == self._buffer.maxlen:
                self._dropped += 1  # deque discards the oldest for us
            self._buffer.append(event)
            accepted += 1
        if accepted:
            self._ensure_drainer()
        return accepted

    async def flush(self) -> int:
        """Drain the buffer now and return how many rows were written.

        Awaited by the read endpoints before they query, so a dashboard opened
        immediately after a judge run shows that run — the alternative is a
        demo where the numbers appear a few seconds late for no visible reason.
        """
        if self._client is None:
            return 0
        written = 0
        while self._buffer:
            batch = [self._buffer.popleft() for _ in range(min(self._batch_size, len(self._buffer)))]
            count = await try_insert(self._client, batch)
            if count == 0 and batch:
                # The write was lost (cluster down). Do not requeue: retrying a
                # batch that just failed is how a queue turns into a stall, and
                # the API must stay responsive.
                self._failed += len(batch)
            written += count
        self._written += written
        return written

    async def aclose(self) -> None:
        """Stop the drainer, make a last attempt to flush, close the session."""
        self._closed = True
        drainer, self._drainer = self._drainer, None
        if drainer is not None and not drainer.done():
            drainer.cancel()
            await asyncio.gather(drainer, return_exceptions=True)
        try:
            await self.flush()
        except Exception as exc:  # never let shutdown telemetry raise
            logger.warning("analytics final flush failed: %s", exc)
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                logger.debug("analytics runner close failed: %s", exc)

    # -- internals ---------------------------------------------------------
    def _ensure_drainer(self) -> None:
        """Start the background drain task, if we are inside an event loop.

        ``record`` is sync and may be called from anywhere, so a missing loop is
        expected rather than exceptional: the events stay buffered and the next
        ``flush()`` (or the next record inside a request) picks them up.
        """
        if self._drainer is not None and not self._drainer.done():
            if self._wake is not None:
                self._wake.set()
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._wake = asyncio.Event()
        self._drainer = loop.create_task(self._drain_loop(), name="analytics-drain")

    async def _drain_loop(self) -> None:
        assert self._wake is not None
        while not self._closed:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._flush_interval_s)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if not self._buffer:
                continue
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - flush already swallows
                logger.warning("analytics drain failed: %s", exc)


# --------------------------------------------------------------------------- #
# Process-wide instance
# --------------------------------------------------------------------------- #
# One recorder per process, mirroring how app.api.deps keeps one repository.
# Built lazily so importing this module never reads the environment or spawns
# anything, and replaceable so tests can install a double.
_recorder: EventRecorder | None = None


def build_recorder(
    settings: AnalyticsSettings | None = None,
    runner: QueryRunner | None = None,
) -> EventRecorder:
    """Construct a recorder from settings, with an optional injected runner.

    When ClickHouse is not configured the recorder is built *disabled* rather
    than not built at all, so every call site has the same shape whether or not
    a cluster exists.
    """
    settings = settings or AnalyticsSettings.from_env()
    if not settings.enabled and runner is None:
        return EventRecorder(None)
    transport = runner or ClickHouseMCPRunner(settings)
    client = AnalyticsClient(transport, settings.database)
    return EventRecorder(
        client,
        flush_interval_s=settings.flush_interval_s,
        batch_size=settings.batch_size,
        buffer_max=settings.buffer_max,
    )


def get_recorder() -> EventRecorder:
    """The process-wide recorder, built from the environment on first use."""
    global _recorder
    if _recorder is None:
        _recorder = build_recorder()
    return _recorder


def set_recorder(recorder: EventRecorder | None) -> None:
    """Install (or clear) the process-wide recorder. Tests use this."""
    global _recorder
    _recorder = recorder


async def shutdown_recorder() -> None:
    """Flush and close the process-wide recorder; safe to call when unused."""
    global _recorder
    recorder, _recorder = _recorder, None
    if recorder is not None:
        await recorder.aclose()


__all__ = [
    "ClickHouseUnavailable",
    "EventRecorder",
    "build_recorder",
    "get_recorder",
    "set_recorder",
    "shutdown_recorder",
]
