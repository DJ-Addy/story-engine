"""GeminiImageAdapter: storyboard frames via Gemini image generation on Vertex AI.

The ``ImageProvider`` implementation behind ``POST .../shots/{n}/board``. One
call, one still frame; the frame is persisted as the shot's board and the video
renderer (``app.api.routers.renders``) then switches Veo to image-to-video on
its own, which is why a board is the cheap first half of the animatic pipeline.

Same transport as ``app.adapters.gemini``: Vertex AI REST over ``aiohttp``, an
ADC bearer token from ``app.adapters.google_auth``, no ``google-genai`` SDK.
Image models are ordinary ``generateContent`` models here — the only differences
from the text adapter are ``responseModalities: ["IMAGE"]`` in the request and
an ``inlineData`` part instead of ``text`` in the reply — so the two adapters
deliberately share their shape rather than a base class: each stays readable on
its own and neither can break the other.

Region: Gemini image models are served from the ``global`` endpoint only in
this project (regional endpoints 404), so the location default is ``global``
and ``GOOGLE_GEMINI_LOCATION`` is honoured purely for parity with the text
adapter.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/.
Construction never fails on missing credentials (so the module imports and
tests collect offline); ``generate`` raises ``TerminalProviderError`` only when
actually invoked without them.

Docs: https://cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/2-5-flash-image
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os

import aiohttp

from app.adapters.base import (
    ImageResult,
    RetryableProviderError,
    TerminalProviderError,
    classify_http_status,
)
from app.adapters.google_auth import GoogleTokenSource, TokenSource

# gemini-2.5-flash-image is the fast/cheap image model; gemini-3-pro-image also
# works on this endpoint. Override with GOOGLE_GEMINI_IMAGE_MODEL.
_DEFAULT_MODEL = "gemini-2.5-flash-image"

_DEFAULT_LOCATION = "global"

# Cost governor input, NOT billing. The list price is about $0.039 per image
# (1290 output tokens at $30/1M), so 4 cents is an ESTIMATE that errs high;
# ``generate`` reports the same figure because the API bills per image, not
# per byte, and a per-token re-price would only pretend to more precision.
_CENTS_PER_IMAGE = 4

# Boards are cut against a 16:9 timeline and Veo is handed the frame as-is, so
# ask for that shape rather than cropping later. ``imageConfig.aspectRatio`` is
# documented for this model; if a future model rejects it, this one constant is
# the whole change.
_IMAGE_CONFIG: dict = {"aspectRatio": "16:9"}

_NETWORK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


class GeminiImageAdapter:
    name = "gemini-image"

    # Reference-conditioned generation is not wired up today (``generate_from_refs``
    # raises); the protocol still wants the attribute, and 0 says exactly that.
    max_reference_images = 0

    def __init__(
        self,
        token_source: TokenSource | None = None,
        project: str | None = None,
        location: str | None = None,
        model: str | None = None,
    ) -> None:
        # Never resolve credentials here: the module must import and tests must
        # collect with no GCP setup. Resolution happens on first generate().
        self._tokens: TokenSource = token_source or GoogleTokenSource(project=project)
        self._location = (
            location or os.environ.get("GOOGLE_GEMINI_LOCATION") or _DEFAULT_LOCATION
        )
        self._model = (
            model or os.environ.get("GOOGLE_GEMINI_IMAGE_MODEL") or _DEFAULT_MODEL
        )

    @property
    def model(self) -> str:
        return self._model

    def _url(self, model: str) -> str:
        # The "global" endpoint has no region prefix on the host; every regional
        # endpoint does.
        project = self._tokens.project()
        host = (
            "aiplatform.googleapis.com"
            if self._location == "global"
            else f"{self._location}-aiplatform.googleapis.com"
        )
        return (
            f"https://{host}/v1/projects/{project}/locations/{self._location}"
            f"/publishers/google/models/{model}:generateContent"
        )

    async def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._tokens.token()}",
            "Content-Type": "application/json; charset=utf-8",
        }

    @staticmethod
    def _raise_for_status(status: int, data: bytes) -> None:
        """Raise the right provider error for a non-2xx Vertex response."""
        if 200 <= status < 300:
            return
        snippet = data[:200].decode("utf-8", "replace").strip()
        message = f"gemini-image http {status}: {snippet}"
        if classify_http_status(status) == "retryable":
            raise RetryableProviderError(message)
        raise TerminalProviderError(message)

    @staticmethod
    def _extract_image(payload: dict) -> tuple[bytes, str]:
        """Decode the first ``inlineData`` part of the first candidate.

        Returns ``(image_bytes, mime_type)``. The model may put a text part
        ahead of the image (a caption, a refusal) so the parts are scanned, not
        indexed. Every "no image" outcome is terminal: a safety block, a
        non-STOP finish and a text-only answer all come back identically for an
        identical prompt, so a retry would only burn quota.
        """
        candidates = payload.get("candidates") or []
        if not candidates:
            reason = (payload.get("promptFeedback") or {}).get("blockReason")
            raise TerminalProviderError(
                "gemini-image returned no candidates"
                + (f" (blocked: {reason})" if reason else "")
            )
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        for part in parts:
            inline = part.get("inlineData") or {}
            encoded = inline.get("data")
            if not encoded:
                continue
            try:
                image_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise TerminalProviderError(
                    f"gemini-image returned undecodable image data: {exc}"
                ) from exc
            if not image_bytes:
                break
            return image_bytes, str(inline.get("mimeType") or "image/png")
        raise TerminalProviderError(
            f"gemini-image returned no image part "
            f"(finishReason {candidate.get('finishReason')!r})"
        )

    async def generate(self, prompt: str, seed: int, params: dict) -> ImageResult:
        """Render one frame from text.

        ``params`` may carry ``model`` plus any ``generationConfig`` keys; they
        merge into the config and win over the defaults, which is how a caller
        drops or changes ``imageConfig`` without touching this module.

        ``seed`` is recorded on the result but not sent: the request shape
        below is the one verified against the live endpoint, and an unrecognised
        field would turn every board into a 503 on the day it matters. Gemini's
        seed is best-effort anyway, so nothing upstream relies on it.
        """
        params = dict(params or {})
        model = params.pop("model", None) or self._model

        generation_config: dict = {
            "responseModalities": ["IMAGE"],
            "imageConfig": dict(_IMAGE_CONFIG),
            **params,
        }
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }

        headers = await self._headers()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self._url(model), json=body, headers=headers) as resp:
                    data = await resp.read()
                    self._raise_for_status(resp.status, data)
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"gemini-image network failure: {exc}") from exc

        try:
            payload = json.loads(data)
        except ValueError as exc:
            raise TerminalProviderError(
                f"gemini-image returned non-JSON body: {exc}"
            ) from exc

        image_bytes, mime_type = self._extract_image(payload)
        return ImageResult(
            image_bytes=image_bytes,
            cost_cents=self.estimate_cost_cents(1),
            provider=self.name,
            model=model,
            seed=seed,
            gen_params={"mime_type": mime_type, **generation_config},
        )

    async def generate_from_refs(
        self, prompt: str, refs: list[bytes], seed: int, params: dict
    ) -> ImageResult:
        raise NotImplementedError(
            "gemini-image does not support reference-conditioned generation yet; "
            "use generate() for a text-only storyboard frame"
        )

    def estimate_cost_cents(self, n: int) -> int:
        # Flat per-image pricing, so no floor is needed: zero images cost zero.
        return n * _CENTS_PER_IMAGE
