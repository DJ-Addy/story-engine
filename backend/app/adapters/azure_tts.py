"""AzureTTSAdapter: Azure Neural TTS with REAL emotion via SSML express-as.

This is the emotion-capable upgrade over the keyless ``EdgeTTSAdapter`` (which
only approximates emotion with rate/pitch nudges). Azure neural voices support
native expressive styles through ``<mstts:express-as style="...">``, so a line
tagged ``angry`` is actually delivered angrily rather than merely faster/higher.

Talks to the Azure Speech REST endpoint over ``aiohttp`` (an installed dep) —
the ``azure-cognitiveservices-speech`` SDK is deliberately NOT used, keeping the
dependency surface small and the transport trivial to mock in tests.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/.
Credentials come from the constructor or the ``AZURE_SPEECH_KEY`` /
``AZURE_SPEECH_REGION`` env vars. Construction never fails on missing creds
(so the module imports and tests collect offline); ``synthesize`` raises a
``TerminalProviderError`` only when actually invoked without them.
"""

from __future__ import annotations

import asyncio
import io
import os
from math import ceil
from xml.sax.saxutils import escape

import aiohttp
import numpy as np
import soundfile as sf

from app.adapters.base import (
    RetryableProviderError,
    TerminalProviderError,
    TTSResult,
    Voice,
    classify_http_status,
)
from app.adapters.edge import prosody_for  # read-only reuse of the rate/pitch deltas
from app.nlp.emotion import EMOTIONS

# Canonical emotion -> Azure expressive style name. Unlike edge-tts's prosody
# hack, these are real delivery styles the neural voices were trained on. Style
# availability is voice-dependent (Aria/Jenny support the widest set); a voice
# that lacks a requested style falls back to neutral on Azure's side, so this is
# always safe to send. Keys must cover exactly app.nlp.emotion.EMOTIONS.
_EMOTION_STYLE: dict[str, str] = {
    "angry": "angry",
    "happy": "cheerful",
    "sad": "sad",
    "afraid": "terrified",
    "excited": "excited",
    "whispering": "whispering",
    "shouting": "shouting",
    "calm": "gentle",
    "serious": "unfriendly",
    "sarcastic": "unfriendly",
    "surprised": "excited",
    "urgent": "excited",
}
assert set(_EMOTION_STYLE) == set(EMOTIONS), "style table drifted from EMOTIONS vocabulary"


def style_for(emotion: str | None) -> str | None:
    """Map a canonical emotion to an Azure expressive style, or ``None``.

    Pure and importable without any network access. ``None`` or an emotion
    outside the canonical vocabulary returns ``None``, signalling the caller to
    fall back to a ``<prosody>`` wrapper instead of ``<mstts:express-as>``.
    """
    if emotion is None:
        return None
    return _EMOTION_STYLE.get(emotion)


def build_ssml(text: str, voice_id: str, emotion: str | None, params: dict) -> str:
    """Build the SSML document sent to Azure for one utterance.

    The text is XML-escaped. When the emotion maps to an Azure style the body is
    wrapped in ``<mstts:express-as>`` for native expressive delivery; otherwise
    it falls back to a ``<prosody>`` wrapper reusing edge-tts's rate/pitch deltas
    so every emotion (and neutral/None) still does *something*. Explicit
    ``params`` (rate/pitch) win over the emotion defaults in the prosody path.
    """
    escaped = escape(text)
    style = style_for(emotion)
    if style is not None:
        body = f'<mstts:express-as style="{style}">{escaped}</mstts:express-as>'
    else:
        defaults = prosody_for(emotion)
        rate = params.get("rate", defaults.get("rate", "+0%"))
        pitch = params.get("pitch", defaults.get("pitch", "+0Hz"))
        body = f'<prosody rate="{rate}" pitch="{pitch}">{escaped}</prosody>'
    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" '
        'xmlns:mstts="https://www.w3.org/2001/mstts" '
        'xml:lang="en-US">'
        f'<voice name="{voice_id}">{body}</voice>'
        "</speak>"
    )


# Curated static list mirroring edge.py's ids so voices are interchangeable
# between adapters. A live/full catalog would GET
# https://{region}.tts.speech.microsoft.com/cognitiveservices/voices/list
# (which returns every neural voice and its supported StyleList).
_CURATED_VOICES: list[Voice] = [
    Voice(id="en-US-GuyNeural", name="Guy (US)", tags=["male", "narrator", "en-US", "expressive"]),
    Voice(
        id="en-US-ChristopherNeural",
        name="Christopher (US)",
        tags=["male", "narrator", "en-US", "expressive"],
    ),
    Voice(
        id="en-US-JennyNeural",
        name="Jenny (US)",
        tags=["female", "conversational", "en-US", "expressive"],
    ),
    Voice(
        id="en-US-AriaNeural",
        name="Aria (US)",
        tags=["female", "expressive", "en-US", "styles"],
    ),
    Voice(id="en-GB-RyanNeural", name="Ryan (UK)", tags=["male", "conversational", "en-GB"]),
    Voice(id="en-GB-SoniaNeural", name="Sonia (UK)", tags=["female", "conversational", "en-GB"]),
]

_NETWORK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)

_ENDPOINT = "https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
_OUTPUT_FORMAT = "riff-24khz-16bit-mono-pcm"


def _decode_wav(wav: bytes) -> tuple[np.ndarray, int]:
    """Decode RIFF/WAV bytes to mono float32 samples via libsndfile."""
    samples, sr = sf.read(io.BytesIO(wav), dtype="float32", always_2d=True)
    return samples.mean(axis=1).astype(np.float32), int(sr)


class AzureTTSAdapter:
    name = "azure"

    def __init__(self, subscription_key: str | None = None, region: str | None = None) -> None:
        # Fall back to env vars but never raise here: the module must import and
        # tests must collect without credentials. The check happens in synthesize.
        self._key = subscription_key or os.environ.get("AZURE_SPEECH_KEY")
        self._region = region or os.environ.get("AZURE_SPEECH_REGION")

    def _require_credentials(self) -> tuple[str, str]:
        if not self._key or not self._region:
            raise TerminalProviderError(
                "azure tts requires a subscription key and region "
                "(constructor args or AZURE_SPEECH_KEY / AZURE_SPEECH_REGION)"
            )
        return self._key, self._region

    async def _post_ssml(self, ssml: str) -> bytes:
        """POST SSML to Azure and return the raw RIFF/WAV response bytes.

        Raises RetryableProviderError for 429/5xx and network faults,
        TerminalProviderError for other non-2xx statuses.
        """
        key, region = self._require_credentials()
        url = _ENDPOINT.format(region=region)
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": _OUTPUT_FORMAT,
            "User-Agent": "story-engine",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=ssml.encode("utf-8"), headers=headers) as resp:
                    body = await resp.read()
                    if not 200 <= resp.status < 300:
                        snippet = body[:200].decode("utf-8", "replace").strip()
                        message = f"azure tts http {resp.status}: {snippet}"
                        if classify_http_status(resp.status) == "retryable":
                            raise RetryableProviderError(message)
                        raise TerminalProviderError(message)
                    return body
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"azure tts network failure: {exc}") from exc

    async def synthesize(
        self, text: str, voice_id: str, emotion: str | None, params: dict
    ) -> TTSResult:
        self._require_credentials()  # fail fast + offline-friendly
        ssml = build_ssml(text, voice_id, emotion, params)
        wav = await self._post_ssml(ssml)
        try:
            samples, sr = _decode_wav(wav)
        except (sf.LibsndfileError, RuntimeError) as exc:
            raise TerminalProviderError(f"undecodable azure tts audio: {exc}") from exc
        return TTSResult(
            audio_bytes=wav,
            duration_ms=round(len(samples) / sr * 1000),
            cost_cents=self.estimate_cost_cents(text),
            provider=self.name,
            model=voice_id,
            gen_params={
                "voice_id": voice_id,
                "emotion": emotion,
                "style": style_for(emotion),
                "format": "wav",
                "sample_rate": sr,
                **params,
            },
        )

    async def list_voices(self) -> list[Voice]:
        # Static/curated (no network). A live list would hit the
        # /cognitiveservices/voices/list endpoint for the region.
        return list(_CURATED_VOICES)

    def estimate_cost_cents(self, text: str) -> int:
        # Azure Neural TTS standard tier bills ~US$16 per 1,000,000 characters
        # = $0.000016/char = 0.0016 cents/char. Round up, floor of 1 cent so a
        # request is never free in the cost governor's ledger.
        return max(1, ceil(len(text) * 0.0016))
