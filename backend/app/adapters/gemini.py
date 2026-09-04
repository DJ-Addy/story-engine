"""GeminiAdapter: Gemini text completion via Vertex AI's ``generateContent``.

The ``LLMProvider`` implementation for every production path that currently
accepts an optional LLM: novel->screenplay conversion (``app.ingest.novel``),
shot-list generation (``app.shotlist.generate``), and the rubric-based judges
(``app.judge.voices`` / ``app.judge.ranking``). Those all take ``llm=None`` and
degrade to deterministic heuristics, so wiring this adapter in is what turns the
"AI judge" features on.

Talks to the Vertex AI REST API over ``aiohttp`` (an installed dep); only
``google-auth`` is used, for ADC bearer tokens (see ``app.adapters.google_auth``).
The ``google-genai`` SDK is deliberately NOT used, keeping the dependency surface
small and the transport trivial to mock in tests.

Gemini's ``system`` prompt is a first-class field (``systemInstruction``) rather
than a leading user turn, so the protocol's (system, user) split maps cleanly.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/.
Construction never fails on missing credentials (so the module imports and tests
collect offline); ``complete`` raises a ``TerminalProviderError`` only when
actually invoked without them.

Docs: https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-8-flash
"""

from __future__ import annotations

import asyncio
import json
import os
from math import ceil

import aiohttp

from app.adapters.base import (
    LLMResult,
    RetryableProviderError,
    TerminalProviderError,
    classify_http_status,
)
from app.adapters.google_auth import GoogleTokenSource, TokenSource

# Gemini 3.8 Flash is GA (released 2026-09-02), fast and cheap, supports system
# instructions and structured output, and is served from the multi-region
# "global" endpoint. Override with GOOGLE_GEMINI_MODEL.
_DEFAULT_MODEL = "gemini-3.8-flash"

# Gemini serves from "global" (lowest latency, best availability); Veo does not,
# which is why this adapter keeps its own location knob.
_DEFAULT_LOCATION = "global"

# Cost governor input (NOT billing-accurate). gemini-3.8-flash global list price
# is $0.75 per 1M input tokens and $3.75 per 1M output tokens; these are cents
# per token. Thinking tokens bill as output and are not modelled here.
_INPUT_CENTS_PER_TOKEN = 0.75 * 100 / 1_000_000
_OUTPUT_CENTS_PER_TOKEN = 3.75 * 100 / 1_000_000

# Rough tokenizer stand-in for pre-flight estimates: ~4 characters per token,
# and completions assumed to run ~half the prompt's token count.
_CHARS_PER_TOKEN = 4
_ASSUMED_OUTPUT_RATIO = 0.5

_NETWORK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


class GeminiAdapter:
    name = "gemini"

    def __init__(
        self,
        token_source: TokenSource | None = None,
        project: str | None = None,
        location: str | None = None,
        model: str | None = None,
    ) -> None:
        # Never resolve credentials here: the module must import and tests must
        # collect with no GCP setup. Resolution happens on first complete().
        self._tokens: TokenSource = token_source or GoogleTokenSource(project=project)
        self._location = (
            location or os.environ.get("GOOGLE_GEMINI_LOCATION") or _DEFAULT_LOCATION
        )
        self._model = model or os.environ.get("GOOGLE_GEMINI_MODEL") or _DEFAULT_MODEL

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
        message = f"gemini http {status}: {snippet}"
        if classify_http_status(status) == "retryable":
            raise RetryableProviderError(message)
        raise TerminalProviderError(message)

    @staticmethod
    def _extract_text(payload: dict) -> str:
        """Concatenate the text parts of the first candidate.

        A prompt rejected by safety filters comes back with no candidates and a
        ``promptFeedback.blockReason``; a response truncated by safety comes back
        with a non-STOP ``finishReason``. Both are terminal — retrying an
        identical blocked prompt just burns quota.
        """
        candidates = payload.get("candidates") or []
        if not candidates:
            reason = (payload.get("promptFeedback") or {}).get("blockReason")
            raise TerminalProviderError(
                f"gemini returned no candidates{f' (blocked: {reason})' if reason else ''}"
            )
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        if not text:
            raise TerminalProviderError(
                f"gemini returned an empty completion "
                f"(finishReason {candidate.get('finishReason')!r})"
            )
        return text

    def _cost_cents(self, payload: dict, system: str, user: str) -> int:
        """Bill from the reported token usage, falling back to the estimate."""
        usage = payload.get("usageMetadata") or {}
        prompt_tokens = usage.get("promptTokenCount")
        output_tokens = usage.get("candidatesTokenCount")
        if prompt_tokens is None or output_tokens is None:
            return self.estimate_cost_cents(len(system) + len(user))
        cents = (
            prompt_tokens * _INPUT_CENTS_PER_TOKEN + output_tokens * _OUTPUT_CENTS_PER_TOKEN
        )
        return max(1, ceil(cents))

    async def complete(self, system: str, user: str, params: dict) -> LLMResult:
        """Run one non-streaming completion.

        ``params`` may carry ``model`` plus any ``generationConfig`` keys
        (``temperature``, ``maxOutputTokens``, ``topP``, ``responseMimeType``,
        ``responseSchema``); they merge into the config and win over defaults.
        """
        params = dict(params or {})
        model = params.pop("model", None) or self._model

        body: dict = {"contents": [{"role": "user", "parts": [{"text": user}]}]}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if params:
            body["generationConfig"] = params

        headers = await self._headers()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self._url(model), json=body, headers=headers) as resp:
                    data = await resp.read()
                    self._raise_for_status(resp.status, data)
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"gemini network failure: {exc}") from exc

        try:
            payload = json.loads(data)
        except ValueError as exc:
            raise TerminalProviderError(f"gemini returned non-JSON body: {exc}") from exc

        return LLMResult(
            text=self._extract_text(payload),
            cost_cents=self._cost_cents(payload, system, user),
            provider=self.name,
            model=model,
        )

    def estimate_cost_cents(self, prompt_chars: int) -> int:
        # Pre-flight estimate only; ``complete`` re-prices from the response's
        # usageMetadata. Floor of 1 cent so a request is never free in the cost
        # governor's ledger.
        prompt_tokens = prompt_chars / _CHARS_PER_TOKEN
        cents = prompt_tokens * (
            _INPUT_CENTS_PER_TOKEN + _ASSUMED_OUTPUT_RATIO * _OUTPUT_CENTS_PER_TOKEN
        )
        return max(1, ceil(cents))
