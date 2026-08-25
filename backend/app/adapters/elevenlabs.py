"""ElevenLabsAdapter: ElevenLabs TTS with emotion via per-utterance voice_settings.

The emotion-capable sibling of ``AzureTTSAdapter``. Where Azure encodes delivery
in SSML ``<mstts:express-as>`` styles, ElevenLabs exposes a small set of
generation knobs (``stability``, ``similarity_boost``, ``style``,
``use_speaker_boost``) — so a line tagged ``angry`` is rendered less stably and
more stylistically than a ``calm`` one.

Talks to the ElevenLabs REST endpoint over ``aiohttp`` (an installed dep) — the
official ``elevenlabs`` SDK is deliberately NOT used, keeping the dependency
surface small and the transport trivial to mock in tests.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/.
Credentials come from the constructor or the ``ELEVENLABS_API_KEY`` /
``ELEVENLABS_VOICE_ID`` env vars. Construction never fails on a missing key (so
the module imports and tests collect offline); ``synthesize`` raises a
``TerminalProviderError`` only when actually invoked without one.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from math import ceil

import aiohttp
import numpy as np

from app.adapters.base import (
    RetryableProviderError,
    TerminalProviderError,
    TTSResult,
    Voice,
    classify_http_status,
)
from app.render.audio import dsp  # wav_bytes + SR=24000; ties us to pcm_24000 output

# Default synthesis model. ElevenLabs' multilingual v2 supports the full
# voice_settings surface (including ``style``) that the emotion mapping needs.
_DEFAULT_MODEL = "eleven_multilingual_v2"

# Rachel — a public/default ElevenLabs voice, used when no voice is configured.
_DEFAULT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"

# ElevenLabs returns 16-bit LE mono PCM at 24 kHz via ?output_format=pcm_24000,
# which matches the pipeline's dsp.SR so no resampling is needed.
_SR = dsp.SR

# Curated static list (NO network). ElevenLabs voice ids are opaque ~20-char
# alphanumeric handles, unlike Edge/Azure locale names. A live/full catalog
# would GET https://api.elevenlabs.io/v1/voices (returns every voice + labels).
_CURATED_VOICES: list[Voice] = [
    Voice(id="21m00Tcm4TlvDq8ikWAM", name="Rachel", tags=["female", "narrator", "calm", "american"]),
    Voice(id="pNInz6obpgDQGcFmaJgB", name="Adam", tags=["male", "narrator", "deep", "american"]),
    Voice(
        id="ErXwobaYiN019PkySvjV",
        name="Antoni",
        tags=["male", "conversational", "warm", "american"],
    ),
    Voice(
        id="EXAVITQu4vr4xnSDxMaL",
        name="Bella",
        tags=["female", "conversational", "soft", "american"],
    ),
    Voice(id="AZnzlk1XvdvUeBnXmlld", name="Domi", tags=["female", "expressive", "strong", "american"]),
]

_CURATED_IDS: list[str] = [voice.id for voice in _CURATED_VOICES]

# Edge/Azure voice ids look like "en-US-JennyNeural": two-letter language, a
# hyphen, two-letter region, a hyphen, then the voice name.
_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}-")

# High-arousal emotions push toward a less stable, more stylized delivery;
# low-arousal (calm/sad/serious) toward a steadier, plainer one. These knobs are
# tuned by ear, not science — adjust freely as long as the direction stays sane.
_HIGH_AROUSAL = frozenset({"angry", "shouting", "excited", "afraid", "surprised", "urgent"})
_LOW_AROUSAL = frozenset({"calm", "sad", "serious"})

_NETWORK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)

_ENDPOINT = "https://api.elevenlabs.io/v1/text-to-speech/{voice}"
_OUTPUT_FORMAT = "pcm_24000"


def voice_settings_for(emotion: str | None) -> dict:
    """Map a canonical emotion to ElevenLabs ``voice_settings``.

    Pure and importable without any network access. Always returns all four
    keys — ``stability``, ``similarity_boost``, ``style``, ``use_speaker_boost``
    — so the body shape is stable. ``None``/neutral (and any unknown label)
    yields a plain, moderately-stable delivery.
    """
    if emotion in _HIGH_AROUSAL:
        stability, style = 0.3, 0.6
    elif emotion in _LOW_AROUSAL:
        stability, style = 0.7, 0.15
    elif emotion == "whispering":
        stability, style = 0.5, 0.2
    elif emotion in ("happy", "sarcastic"):
        # Positive/dry tones: mildly expressive middle ground between the poles.
        stability, style = 0.4, 0.4
    else:  # None, "neutral", or anything outside the canonical vocabulary
        stability, style = 0.5, 0.0
    return {
        "stability": stability,
        "similarity_boost": 0.75,
        "style": style,
        "use_speaker_boost": True,
    }


def resolve_voice_id(voice_id: str) -> str:
    """Resolve a requested voice to a concrete ElevenLabs voice id.

    Pure and importable without any network access. A real ElevenLabs id (opaque
    ~20-char handle) passes through unchanged. An Edge/Azure-style locale name
    (e.g. ``en-US-JennyNeural``) or an empty string is mapped *deterministically*
    into the curated pool via a hash — so distinct character names resolve to
    distinct ElevenLabs voices even though callers currently pass Edge-style
    names, and the same name always resolves to the same voice.
    """
    if voice_id and not _LOCALE_RE.match(voice_id):
        return voice_id
    digest = hashlib.sha256((voice_id or "").encode("utf-8")).digest()
    index = int.from_bytes(digest[:8], "little") % len(_CURATED_IDS)
    return _CURATED_IDS[index]


class ElevenLabsAdapter:
    name = "elevenlabs"

    def __init__(self, api_key: str | None = None, default_voice_id: str | None = None) -> None:
        # Fall back to env vars but never raise here: the module must import and
        # tests must collect without a key. The check happens in synthesize.
        self._key = api_key or os.environ.get("ELEVENLABS_API_KEY")
        self._default_voice_id = (
            default_voice_id or os.environ.get("ELEVENLABS_VOICE_ID") or _DEFAULT_VOICE_ID
        )

    def _require_key(self) -> str:
        if not self._key:
            raise TerminalProviderError(
                "elevenlabs tts requires an api key "
                "(constructor arg or ELEVENLABS_API_KEY)"
            )
        return self._key

    async def _post_pcm(self, voice: str, body: dict) -> bytes:
        """POST one utterance to ElevenLabs and return raw PCM response bytes.

        Raises RetryableProviderError for 429/5xx and network faults,
        TerminalProviderError for other non-2xx statuses.
        """
        key = self._require_key()
        url = f"{_ENDPOINT.format(voice=voice)}?output_format={_OUTPUT_FORMAT}"
        headers = {
            "xi-api-key": key,
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=body, headers=headers) as resp:
                    data = await resp.read()
                    if not 200 <= resp.status < 300:
                        snippet = data[:200].decode("utf-8", "replace").strip()
                        message = f"elevenlabs tts http {resp.status}: {snippet}"
                        if classify_http_status(resp.status) == "retryable":
                            raise RetryableProviderError(message)
                        raise TerminalProviderError(message)
                    return data
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"elevenlabs tts network failure: {exc}") from exc

    async def synthesize(
        self, text: str, voice_id: str, emotion: str | None, params: dict
    ) -> TTSResult:
        self._require_key()  # fail fast + offline-friendly (raises before any network)
        voice = resolve_voice_id(voice_id or self._default_voice_id)
        model_id = params.get("model_id", _DEFAULT_MODEL)
        settings = voice_settings_for(emotion)
        body = {"text": text, "model_id": model_id, "voice_settings": settings}
        data = await self._post_pcm(voice, body)

        # Raw PCM: 16-bit signed little-endian mono @ 24 kHz -> float32 in [-1, 1).
        samples = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
        audio_bytes = dsp.wav_bytes(samples, _SR)
        return TTSResult(
            audio_bytes=audio_bytes,
            duration_ms=round(len(samples) / _SR * 1000),
            cost_cents=self.estimate_cost_cents(text),
            provider=self.name,
            model=model_id,
            gen_params={
                "voice_id": voice,
                "emotion": emotion,
                "voice_settings": settings,
                **params,
            },
        )

    async def list_voices(self) -> list[Voice]:
        # Static/curated (no network). A live list would GET
        # https://api.elevenlabs.io/v1/voices for the account's full catalog.
        return list(_CURATED_VOICES)

    def estimate_cost_cents(self, text: str) -> int:
        # ElevenLabs standard tiers bill ~US$0.30 per 1,000 characters
        # = $0.0003/char = 0.03 cents/char. Round up, floor of 1 cent so a
        # request is never free in the cost governor's ledger.
        return max(1, ceil(len(text) * 0.03))
