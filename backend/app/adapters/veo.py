"""VeoAdapter: image/text -> video via Veo on Vertex AI's long-running predict API.

The video-renderer sibling of the one-shot TTS adapters, and a drop-in for the
retired Runway adapter: same ``VideoProvider`` protocol, same ``VideoResult``
contract, so ``app.api.routers.renders`` needs no changes. Veo does not return a
clip synchronously — a generation is *submitted* to ``:predictLongRunning``
(returns an operation name), *polled* via ``:fetchPredictOperation`` until
``done``, and the finished clip is then read from the operation's response. The
primary use is turning an animatic frame into a moving clip (image-to-video);
text-to-video is the prompt-only fallback.

Talks to the Vertex AI REST API over ``aiohttp`` (an installed dep); only
``google-auth`` is used, for ADC bearer tokens (see ``app.adapters.google_auth``).
The ``google-genai`` SDK is deliberately NOT used, keeping the dependency surface
small and the transport trivial to mock in tests.

Two Veo quirks the adapter absorbs so callers never see them:

* Veo 3 accepts only 4, 6 or 8 second clips, while the API's ``duration_s`` is a
  free integer — ``_snap_duration`` rounds to the nearest legal value and the
  returned ``duration_ms`` reports what was *actually* generated, not what was asked.
* Omitting ``storageUri`` makes Veo return the MP4 inline as base64, which is what
  we want (no bucket to provision); passing ``params["storage_uri"]`` switches to
  GCS delivery and the result then carries ``gs://`` URLs instead of bytes.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/.
Construction never fails on missing credentials (so the module imports and tests
collect offline); ``generate`` raises a ``TerminalProviderError`` only when
actually invoked without them.

The poll loop is *bounded* and drives its waits through an injected ``sleep``
callable and ``clock`` function, so tests can run it instantly with zero real
delay (never ``asyncio.sleep`` on a real timer that tests can't stub).

Docs: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/veo/3-1-generate
      https://docs.cloud.google.com/vertex-ai/generative-ai/docs/video/generate-videos-from-an-image
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
from app.adapters.google_auth import GoogleTokenSource, TokenSource

# Veo is region-pinned; us-central1 is the only region serving the 3.x models.
_DEFAULT_LOCATION = "us-central1"

# Default generation model: Veo 3.1 is GA (released 2025-11-17), does both
# text-to-video and image-to-video, and generates synchronized audio.
# NOTE: veo-3.0-generate-001 reached its retirement date on 2026-06-30 and must
# not be used as a default. Override with GOOGLE_VEO_MODEL.
_DEFAULT_MODEL = "veo-3.1-generate-001"

# Clip lengths Veo 3 accepts, in seconds. Anything else is a 400.
_ALLOWED_DURATIONS = (4, 6, 8)

# Veo requires an explicit ratio and only accepts these two.
_DEFAULT_ASPECT_RATIO = "16:9"

# Cost governor input (NOT billing-accurate). Vertex bills Veo per second of
# generated video; these are the with-audio list rates in cents/second, which is
# the conservative choice for a cost cap because Veo 3.x generates audio by
# default. Video-only generation is half these rates.
_CENTS_PER_SECOND: dict[str, int] = {
    "veo-3.1-generate-001": 40,
    "veo-3.1-fast-generate-001": 10,
    "veo-3.0-generate-001": 40,
    "veo-3.0-fast-generate-001": 10,
    "veo-2.0-generate-001": 50,
}
_FALLBACK_CENTS_PER_SECOND = 40

_NETWORK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


def _snap_duration(duration_s: int) -> int:
    """Round a requested clip length to the nearest length Veo 3 accepts.

    Pure and importable without any network access. Ties round down (5 -> 4,
    7 -> 6) so a request never silently costs more than asked.
    """
    return min(_ALLOWED_DURATIONS, key=lambda allowed: (abs(allowed - duration_s), allowed))


def _to_image_instance(image: bytes | str) -> dict:
    """Coerce an image input into a Veo ``instances[].image`` object.

    Pure and importable without any network access. A ``gs://`` string becomes a
    ``gcsUri``; any other ``str`` is assumed to be base64 already; raw ``bytes``
    are base64-encoded, sniffing magic numbers for the MIME type. Veo accepts
    only JPEG and PNG, so anything unrecognised is declared PNG and left to the
    API to reject with a clear message.
    """
    if isinstance(image, str):
        if image.startswith("gs://"):
            return {"gcsUri": image, "mimeType": "image/png"}
        return {"bytesBase64Encoded": image, "mimeType": "image/png"}
    mime_type = "image/jpeg" if image[:3] == b"\xff\xd8\xff" else "image/png"
    return {
        "bytesBase64Encoded": base64.b64encode(image).decode("ascii"),
        "mimeType": mime_type,
    }


class VeoAdapter:
    name = "veo"

    def __init__(
        self,
        token_source: TokenSource | None = None,
        project: str | None = None,
        location: str | None = None,
        *,
        poll_interval_s: float = 10.0,
        poll_timeout_s: float = 600.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Never resolve credentials here: the module must import and tests must
        # collect with no GCP setup. Resolution happens on first generate.
        self._tokens: TokenSource = token_source or GoogleTokenSource(project=project)
        self._location = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or _DEFAULT_LOCATION
        self._poll_interval_s = poll_interval_s
        self._poll_timeout_s = poll_timeout_s
        # Injected so tests drive the poll loop instantly with a fake clock/sleep
        # and never wait on a real timer.
        self._sleep = sleep
        self._clock = clock

    def _model_url(self, model: str, verb: str) -> str:
        project = self._tokens.project()
        return (
            f"https://{self._location}-aiplatform.googleapis.com/v1/projects/{project}"
            f"/locations/{self._location}/publishers/google/models/{model}:{verb}"
        )

    async def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._tokens.token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    @staticmethod
    def _raise_for_status(status: int, data: bytes, op: str) -> None:
        """Raise the right provider error for a non-2xx Vertex response."""
        if 200 <= status < 300:
            return
        snippet = data[:200].decode("utf-8", "replace").strip()
        message = f"veo {op} http {status}: {snippet}"
        if classify_http_status(status) == "retryable":
            raise RetryableProviderError(message)
        raise TerminalProviderError(message)

    @staticmethod
    def _parse_json(data: bytes, op: str) -> dict:
        try:
            return json.loads(data)
        except ValueError as exc:
            raise TerminalProviderError(f"veo {op} returned non-JSON body: {exc}") from exc

    async def _post(
        self, session: aiohttp.ClientSession, url: str, body: dict, op: str
    ) -> dict:
        """POST one Vertex request and return its parsed JSON body."""
        headers = await self._headers()
        try:
            async with session.post(url, json=body, headers=headers) as resp:
                data = await resp.read()
                self._raise_for_status(resp.status, data, op)
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"veo {op} network failure: {exc}") from exc
        return self._parse_json(data, op)

    async def _poll_until_done(
        self, session: aiohttp.ClientSession, model: str, operation_name: str
    ) -> dict:
        """Poll an operation until ``done``, within the configured time bound.

        Returns the finished operation payload. Raises TerminalProviderError when
        the operation reports an error and RetryableProviderError if the bound
        elapses first. Waits go through the injected ``sleep`` and the bound is
        measured with the injected ``clock`` — no real timer.
        """
        url = self._model_url(model, "fetchPredictOperation")
        deadline = self._clock() + self._poll_timeout_s
        while True:
            operation = await self._post(session, url, {"operationName": operation_name}, "poll")
            if operation.get("done"):
                error = operation.get("error")
                if error:
                    raise TerminalProviderError(
                        f"veo operation {operation_name} failed: "
                        f"{error.get('message') or error}"
                    )
                return operation
            if self._clock() >= deadline:
                raise RetryableProviderError(
                    f"veo operation {operation_name} did not finish within "
                    f"{self._poll_timeout_s}s"
                )
            await self._sleep(self._poll_interval_s)

    @staticmethod
    def _extract_videos(operation: dict, operation_name: str) -> tuple[bytes, list[str]]:
        """Pull the clip bytes and/or GCS URLs out of a finished operation.

        Veo returns inline base64 when no ``storageUri`` was requested and
        ``gcsUri`` when one was, so exactly one of the two is normally populated.
        A response carrying neither, with a non-zero RAI filter count, means the
        prompt was blocked by safety filters — a terminal, never-retry failure.
        """
        response = operation.get("response") or {}
        videos = response.get("videos") or []
        if not videos:
            filtered = response.get("raiMediaFilteredCount") or 0
            if filtered:
                reasons = response.get("raiMediaFilteredReasons") or []
                raise TerminalProviderError(
                    f"veo filtered all {filtered} sample(s) for safety: {reasons}"
                )
            raise TerminalProviderError(
                f"veo operation {operation_name} finished with no videos: {response}"
            )

        urls = [video["gcsUri"] for video in videos if video.get("gcsUri")]
        encoded = videos[0].get("bytesBase64Encoded")
        if not encoded:
            return b"", urls
        try:
            return base64.b64decode(encoded), urls
        except (ValueError, TypeError) as exc:
            raise TerminalProviderError(f"veo returned non-base64 video bytes: {exc}") from exc

    async def generate(
        self,
        prompt: str | None = None,
        *,
        image: bytes | str | None = None,
        duration_s: int = 5,
        model: str | None = None,
        params: dict | None = None,
    ) -> VideoResult:
        """Generate a video from an image (primary) and/or a text prompt.

        Submits the job, polls to completion, and returns the clip. Pass ``image``
        (raw bytes, base64, or a ``gs://`` URI) for image-to-video, or ``prompt``
        alone for text-to-video. ``params`` merges into the request ``parameters``
        (e.g. ``{"aspect_ratio": "9:16", "seed": 42, "storage_uri": "gs://b/out/"}``)
        and wins over the defaults. ``duration_s`` is snapped to Veo's legal
        4/6/8 seconds and the result reports the snapped value.
        """
        params = dict(params or {})
        model_id = model or params.pop("model", None) or os.environ.get("GOOGLE_VEO_MODEL")
        model_id = model_id or _DEFAULT_MODEL
        actual_duration_s = _snap_duration(duration_s)

        instance: dict = {}
        if prompt:
            instance["prompt"] = prompt
        if image is not None:
            instance["image"] = _to_image_instance(image)
        if not instance:
            raise TerminalProviderError("veo generate requires an image and/or a prompt")

        parameters: dict = {
            "durationSeconds": actual_duration_s,
            "aspectRatio": params.pop("aspect_ratio", _DEFAULT_ASPECT_RATIO),
            "sampleCount": params.pop("sample_count", 1),
        }
        # storageUri is optional and changes the delivery mode: absent => inline
        # base64 bytes (our default), present => the clip lands in the bucket.
        storage_uri = params.pop("storage_uri", None) or os.environ.get("GOOGLE_VEO_STORAGE_URI")
        if storage_uri:
            parameters["storageUri"] = storage_uri
        for key, field in (
            ("negative_prompt", "negativePrompt"),
            ("person_generation", "personGeneration"),
            ("resolution", "resolution"),
            ("seed", "seed"),
            ("generate_audio", "generateAudio"),
        ):
            if key in params:
                parameters[field] = params.pop(key)
        parameters.update(params)  # caller-supplied extras win

        body = {"instances": [instance], "parameters": parameters}
        submit_url = self._model_url(model_id, "predictLongRunning")

        async with aiohttp.ClientSession() as session:
            submitted = await self._post(session, submit_url, body, "submit")
            operation_name = submitted.get("name")
            if not operation_name:
                raise TerminalProviderError(f"veo submit returned no operation name: {submitted}")
            operation = await self._poll_until_done(session, model_id, operation_name)

        video_bytes, output_urls = self._extract_videos(operation, operation_name)

        return VideoResult(
            video_bytes=video_bytes,
            output_urls=output_urls,
            duration_ms=actual_duration_s * 1000,
            cost_cents=self.estimate_cost_cents(actual_duration_s, model_id),
            provider=self.name,
            model=model_id,
            gen_params={
                "operation_name": operation_name,
                "duration_s": actual_duration_s,
                "requested_duration_s": duration_s,
                "has_image": image is not None,
                "prompt": prompt,
                "aspect_ratio": parameters["aspectRatio"],
                "location": self._location,
            },
        )

    def estimate_cost_cents(self, duration_s: int, model: str | None = None) -> int:
        # Per-second list rate for the model, applied to the *snapped* duration
        # so the estimate matches what will actually be generated and billed.
        # Floor of 1 cent so a request is never free in the cost governor's ledger.
        model_id = model or os.environ.get("GOOGLE_VEO_MODEL") or _DEFAULT_MODEL
        rate = _CENTS_PER_SECOND.get(model_id, _FALLBACK_CENTS_PER_SECOND)
        return max(1, ceil(_snap_duration(duration_s) * rate))
