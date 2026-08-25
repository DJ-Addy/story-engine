"""RunwayAdapter: image/text -> video via Runway's async generation API.

The video-renderer sibling of the one-shot TTS adapters. Runway does not return
a clip synchronously: a generation is *submitted* (returns a task id), *polled*
until the task reaches a terminal state, and the finished clip is then *fetched*
from the task's output URL(s). The primary use is turning an animatic frame into
a moving clip (image-to-video); text-to-video is also supported.

Talks to the Runway dev REST API over ``aiohttp`` (an installed dep) — the
official ``runwayml`` SDK is deliberately NOT used, keeping the dependency
surface small and the transport trivial to mock in tests.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/.
Credentials come from the constructor or the ``RUNWAY_API_KEY`` env var.
Construction never fails on a missing key (so the module imports and tests
collect offline); ``generate`` raises a ``TerminalProviderError`` only when
actually invoked without one.

The poll loop is *bounded* and drives its waits through an injected ``sleep``
callable and ``clock`` function, so tests can run it instantly with zero real
delay (never ``asyncio.sleep`` on a real timer that tests can't stub).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from collections.abc import Awaitable, Callable
from math import ceil

import aiohttp

from app.adapters.base import (
    RetryableProviderError,
    TerminalProviderError,
    VideoResult,
    classify_http_status,
)

# Runway dev API base. Both the generation endpoints and the task-status
# endpoint hang off this root.
_API_BASE = "https://api.dev.runwayml.com/v1"

# Runway requires an API-version date header on every request. This is the value
# documented for the dev API; bump it (and re-verify against Runway's changelog)
# when adopting newer request/response fields.
_RUNWAY_VERSION = "2024-11-06"

# Generation endpoints. image-to-video is the primary path (an animatic frame ->
# a moving clip); text-to-video is the promptText-only fallback.
_IMAGE_TO_VIDEO = f"{_API_BASE}/image_to_video"
_TEXT_TO_VIDEO = f"{_API_BASE}/text_to_video"
_TASK_ENDPOINT = f"{_API_BASE}/tasks/{{task_id}}"

# Default generation model. The Gen-4 turbo model is the fast/cheap default that
# the cost estimate below is tuned for; callers can override per-request.
_DEFAULT_MODEL = "gen4_turbo"

# Default aspect ratio (Runway requires an explicit ratio). Overridable via
# ``params["ratio"]``. See Runway's docs for the ratios each model accepts.
_DEFAULT_RATIO = "1280:720"

# Cost governor input (NOT billing-accurate). Runway bills credits per second of
# generated video — roughly ~5 credits/sec on the faster Gen-3/Gen-4 turbo
# models, and credits are ~US$0.01 each, so ~5 cents per generated second. Tune
# this single constant if Runway's pricing or the default model changes.
_CENTS_PER_SECOND = 5

# Runway task lifecycle. PENDING/RUNNING/THROTTLED are non-terminal (keep
# polling); SUCCEEDED yields output URLs; FAILED/CANCELLED are terminal failures.
_SUCCEEDED = "SUCCEEDED"
_TERMINAL_FAILURES = frozenset({"FAILED", "CANCELLED"})

_NETWORK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


def _to_prompt_image(image: bytes | str) -> str:
    """Coerce an image input into a Runway ``promptImage`` value.

    Pure and importable without any network access. A ``str`` (an HTTPS URL or an
    existing data URI) passes through unchanged; raw ``bytes`` are wrapped in a
    base64 ``data:`` URI, sniffing a couple of magic numbers for the media type
    and defaulting to PNG.
    """
    if isinstance(image, str):
        return image
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        media_type = "image/png"
    elif image[:3] == b"\xff\xd8\xff":
        media_type = "image/jpeg"
    elif image[:4] == b"RIFF" and image[8:12] == b"WEBP":
        media_type = "image/webp"
    else:
        media_type = "image/png"
    encoded = base64.b64encode(image).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


class RunwayAdapter:
    name = "runway"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        poll_interval_s: float = 5.0,
        poll_timeout_s: float = 300.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Fall back to the env var but never raise here: the module must import
        # and tests must collect without a key. The check happens in generate.
        self._key = api_key or os.environ.get("RUNWAY_API_KEY")
        self._poll_interval_s = poll_interval_s
        self._poll_timeout_s = poll_timeout_s
        # Injected so tests drive the poll loop instantly with a fake clock/sleep
        # and never wait on a real timer.
        self._sleep = sleep
        self._clock = clock

    def _require_key(self) -> str:
        if not self._key:
            raise TerminalProviderError(
                "runway video requires an api key (constructor arg or RUNWAY_API_KEY)"
            )
        return self._key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._require_key()}",
            "X-Runway-Version": _RUNWAY_VERSION,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_for_status(status: int, data: bytes, op: str) -> None:
        """Raise the right provider error for a non-2xx Runway response."""
        if 200 <= status < 300:
            return
        snippet = data[:200].decode("utf-8", "replace").strip()
        message = f"runway {op} http {status}: {snippet}"
        if classify_http_status(status) == "retryable":
            raise RetryableProviderError(message)
        raise TerminalProviderError(message)

    @staticmethod
    def _parse_json(data: bytes, op: str) -> dict:
        try:
            return json.loads(data)
        except ValueError as exc:
            raise TerminalProviderError(f"runway {op} returned non-JSON body: {exc}") from exc

    async def _submit(self, session: aiohttp.ClientSession, url: str, body: dict) -> str:
        """POST a generation request; return the created task id.

        Raises RetryableProviderError for 429/5xx and network faults,
        TerminalProviderError for other non-2xx statuses or a missing id.
        """
        try:
            async with session.post(url, json=body, headers=self._headers()) as resp:
                data = await resp.read()
                self._raise_for_status(resp.status, data, "submit")
                payload = self._parse_json(data, "submit")
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"runway submit network failure: {exc}") from exc
        task_id = payload.get("id")
        if not task_id:
            raise TerminalProviderError(f"runway submit returned no task id: {payload}")
        return task_id

    async def _get_task(self, session: aiohttp.ClientSession, task_id: str) -> dict:
        """GET one task's current status payload."""
        url = _TASK_ENDPOINT.format(task_id=task_id)
        try:
            async with session.get(url, headers=self._headers()) as resp:
                data = await resp.read()
                self._raise_for_status(resp.status, data, "poll")
                return self._parse_json(data, "poll")
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"runway poll network failure: {exc}") from exc

    async def _poll_until_done(self, session: aiohttp.ClientSession, task_id: str) -> dict:
        """Poll a task until it terminates, within the configured time bound.

        Returns the SUCCEEDED task payload. Raises TerminalProviderError on a
        FAILED/CANCELLED task and RetryableProviderError if the bound elapses
        before the task finishes. Waits go through the injected ``sleep`` and the
        bound is measured with the injected ``clock`` — no real timer.
        """
        deadline = self._clock() + self._poll_timeout_s
        while True:
            task = await self._get_task(session, task_id)
            status = task.get("status")
            if status == _SUCCEEDED:
                return task
            if status in _TERMINAL_FAILURES:
                reason = task.get("failure") or task.get("failureCode") or status
                raise TerminalProviderError(f"runway task {task_id} {status}: {reason}")
            # Non-terminal (PENDING/RUNNING/THROTTLED/unknown): wait and retry,
            # unless we've hit the bound.
            if self._clock() >= deadline:
                raise RetryableProviderError(
                    f"runway task {task_id} did not finish within {self._poll_timeout_s}s "
                    f"(last status {status!r})"
                )
            await self._sleep(self._poll_interval_s)

    async def _download(self, session: aiohttp.ClientSession, url: str) -> bytes:
        """GET a finished clip's bytes from its output URL."""
        try:
            async with session.get(url) as resp:
                data = await resp.read()
                self._raise_for_status(resp.status, data, "download")
                return data
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"runway download network failure: {exc}") from exc

    async def generate(
        self,
        prompt: str | None = None,
        *,
        image: bytes | str | None = None,
        duration_s: int = 5,
        model: str | None = None,
        params: dict | None = None,
        download: bool = True,
    ) -> VideoResult:
        """Generate a video from an image (primary) and/or a text prompt.

        Submits the job, polls to completion, and (by default) downloads the
        resulting clip. Pass ``image`` (raw bytes or a URL/data-URI string) for
        image-to-video, or ``prompt`` alone for text-to-video. ``params`` merges
        into the request body (e.g. ``{"ratio": "1584:672", "seed": 42}``) and
        wins over the defaults. Set ``download=False`` to return URLs only.
        """
        self._require_key()  # fail fast + offline-friendly (raises before any network)
        params = dict(params or {})
        model_id = model or params.pop("model", _DEFAULT_MODEL)

        body: dict = {
            "model": model_id,
            "duration": duration_s,
            "ratio": params.pop("ratio", _DEFAULT_RATIO),
        }
        if image is not None:
            url = _IMAGE_TO_VIDEO
            body["promptImage"] = _to_prompt_image(image)
            if prompt:
                body["promptText"] = prompt
        elif prompt:
            url = _TEXT_TO_VIDEO
            body["promptText"] = prompt
        else:
            raise TerminalProviderError("runway generate requires an image and/or a prompt")
        body.update(params)  # caller-supplied extras win

        async with aiohttp.ClientSession() as session:
            task_id = await self._submit(session, url, body)
            task = await self._poll_until_done(session, task_id)
            output_urls = list(task.get("output") or [])
            if not output_urls:
                raise TerminalProviderError(
                    f"runway task {task_id} succeeded but returned no output url"
                )
            video_bytes = await self._download(session, output_urls[0]) if download else b""

        return VideoResult(
            video_bytes=video_bytes,
            output_urls=output_urls,
            duration_ms=round(duration_s * 1000),
            cost_cents=self.estimate_cost_cents(duration_s, model_id),
            provider=self.name,
            model=model_id,
            gen_params={
                "task_id": task_id,
                "duration_s": duration_s,
                "has_image": image is not None,
                "prompt": prompt,
                "ratio": body["ratio"],
                **params,
            },
        )

    def estimate_cost_cents(self, duration_s: int, model: str | None = None) -> int:
        # ~5 credits/sec on the turbo models * ~US$0.01/credit ≈ 5 cents/sec.
        # ``model`` is accepted for future per-model rates; the flat rate lives in
        # _CENTS_PER_SECOND. Floor of 1 cent so a request is never free in the
        # cost governor's ledger.
        return max(1, ceil(duration_s * _CENTS_PER_SECOND))
