"""GoogleTTSAdapter: Google Cloud Text-to-Speech with REAL emotion via Gemini-TTS.

Gemini-TTS is the only Google TTS family that takes a *natural-language style
instruction* alongside the text: ``input.prompt`` steers tone, pace and delivery
("Whisper the following line..."), which is exactly the emotion control this
product requires (HANDOFF §2 — "voices must have emotions"). Chirp 3: HD voices
share the same voice roster but expose only pace/pause/pronunciation controls, so
they cannot carry the emotion vocabulary and are deliberately not wired up here.

Talks to the Cloud Text-to-Speech REST endpoint over ``aiohttp`` (an installed
dep); only ``google-auth`` is used, for ADC bearer tokens (see
``app.adapters.google_auth``). The heavyweight ``google-cloud-texttospeech``
client is deliberately NOT used, keeping the transport trivial to mock in tests.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/.
Construction never fails on missing credentials (so the module imports and tests
collect offline); ``synthesize`` raises a ``TerminalProviderError`` only when
actually invoked without them.

Docs: https://docs.cloud.google.com/text-to-speech/docs/gemini-tts
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from math import ceil

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
from app.adapters.google_auth import GoogleTokenSource, TokenSource
from app.nlp.emotion import EMOTIONS

_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"

# Gemini-TTS model that renders the style prompt. Both GA models accept
# ``input.prompt``; flash is the cheaper of the two ($0.50/$10.00 per 1M
# text/audio tokens vs $1.00/$20.00 for pro). Override with GOOGLE_TTS_MODEL.
_DEFAULT_MODEL = "gemini-2.5-flash-tts"

# Gemini-TTS voices are bare proper nouns; the locale is a separate field.
_DEFAULT_LANGUAGE_CODE = "en-US"

# LINEAR16 comes back as a RIFF/WAV container, which is what the audio pipeline
# (app.render.audio.pipeline._clip_samples) decodes for exact clip timing.
_AUDIO_ENCODING = "LINEAR16"

# Hard API limits: text and prompt are each capped at 4,000 bytes and their sum
# at 8,000. We check the text (the caller-controlled half) and let the shorter,
# fixed style prompts ride under the combined cap.
_MAX_TEXT_BYTES = 4000

# Canonical emotion -> natural-language style instruction sent as ``input.prompt``.
# Unlike Azure's fixed style enum (which collapsed sarcastic/surprised/urgent onto
# neighbouring styles), a free-text prompt gives every emotion its own distinct
# direction — closer to directing an actor than picking a preset. Keys must cover
# exactly app.nlp.emotion.EMOTIONS.
_EMOTION_STYLE_PROMPT: dict[str, str] = {
    "angry": "Say the following line in a furious, hard-edged tone — clipped, forceful, barely controlled.",
    "happy": "Say the following line brightly and warmly, with an audible smile.",
    "sad": "Say the following line quietly and heavily, weighed down by grief.",
    "afraid": "Say the following line with fear — tight, breathy and unsteady.",
    "excited": "Say the following line with bright, fast, eager energy.",
    "calm": "Say the following line gently and evenly, in an unhurried, soothing tone.",
    "whispering": "Whisper the following line — barely voiced, hushed and intimate.",
    "shouting": "Shout the following line at full volume, projecting hard.",
    "urgent": "Say the following line quickly and insistently, with pressing urgency.",
    "sarcastic": "Say the following line dryly and sardonically, with a mocking lilt.",
    "surprised": "Say the following line with genuine surprise — a sharp, startled lift.",
    "serious": "Say the following line gravely and firmly — low, level and unsmiling.",
    # Worded as enticement rather than seduction on purpose. The model
    # refuses some phrasings outright, and what the Sirens do to Ulysses
    # is luring — an invitation you cannot refuse, not a come-on.
    "seductive": "Say the following line as an enticing invitation — warm, unhurried and honeyed, drawing the listener in.",
}
assert set(_EMOTION_STYLE_PROMPT) == set(EMOTIONS), "style table drifted from EMOTIONS vocabulary"


def style_prompt_for(emotion: str | None) -> str | None:
    """Map a canonical emotion to a Gemini-TTS style instruction, or ``None``.

    Pure and importable without any network access. ``None`` or an emotion
    outside the canonical vocabulary returns ``None``, meaning "send no prompt"
    — the model then reads the line in its default narration voice.
    """
    if emotion is None:
        return None
    return _EMOTION_STYLE_PROMPT.get(emotion)


def build_request(text: str, voice_id: str, emotion: str | None, params: dict) -> dict:
    """Build the ``text:synthesize`` JSON body for one utterance.

    Pure and importable without any network access, so the emotion wiring is
    testable without credentials. ``params`` may override ``language_code``,
    ``model``, ``speaking_rate``, ``pitch``, ``volume_gain_db`` and
    ``sample_rate_hertz``; an explicit ``params["prompt"]`` wins over the
    emotion's style instruction, which is how a caller hand-directs a line.
    """
    if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise TerminalProviderError(
            f"google tts input is {len(text.encode('utf-8'))} bytes, over the "
            f"{_MAX_TEXT_BYTES}-byte limit; split the line before synthesizing"
        )

    synthesis_input: dict = {"text": text}
    prompt = params.get("prompt", style_prompt_for(emotion))
    if prompt:
        synthesis_input["prompt"] = prompt

    audio_config: dict = {"audioEncoding": _AUDIO_ENCODING}
    for key, field in (
        ("speaking_rate", "speakingRate"),
        ("pitch", "pitch"),
        ("volume_gain_db", "volumeGainDb"),
        ("sample_rate_hertz", "sampleRateHertz"),
    ):
        if key in params:
            audio_config[field] = params[key]

    return {
        "input": synthesis_input,
        "voice": {
            "languageCode": params.get(
                "language_code", os.environ.get("GOOGLE_TTS_LANGUAGE_CODE")
            )
            or _DEFAULT_LANGUAGE_CODE,
            "name": voice_id,
            "modelName": params.get("model", os.environ.get("GOOGLE_TTS_MODEL"))
            or _DEFAULT_MODEL,
        },
        "audioConfig": audio_config,
    }


# Curated casting roster drawn from the 28 Gemini-TTS voices. Gender is as
# published by Google; the role tags ("narrator", "lead", ...) are our own
# casting hints for the Casting Studio, not an API attribute.
_CURATED_VOICES: list[Voice] = [
    Voice(id="Charon", name="Charon", tags=["male", "narrator", "en-US", "expressive"]),
    Voice(id="Iapetus", name="Iapetus", tags=["male", "narrator", "en-US", "expressive"]),
    Voice(id="Puck", name="Puck", tags=["male", "lead", "en-US", "expressive"]),
    Voice(id="Enceladus", name="Enceladus", tags=["male", "character", "en-US", "expressive"]),
    Voice(id="Kore", name="Kore", tags=["female", "narrator", "en-US", "expressive"]),
    Voice(id="Aoede", name="Aoede", tags=["female", "lead", "en-US", "expressive"]),
    Voice(id="Leda", name="Leda", tags=["female", "young", "en-US", "expressive"]),
    Voice(id="Callirrhoe", name="Callirrhoe", tags=["female", "character", "en-US", "expressive"]),
]

_NETWORK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


def _decode_wav(wav: bytes) -> tuple[np.ndarray, int]:
    """Decode RIFF/WAV bytes to mono float32 samples via libsndfile."""
    samples, sr = sf.read(io.BytesIO(wav), dtype="float32", always_2d=True)
    return samples.mean(axis=1).astype(np.float32), int(sr)


class GoogleTTSAdapter:
    name = "google-tts"

    def __init__(
        self,
        token_source: TokenSource | None = None,
        project: str | None = None,
    ) -> None:
        # Never resolve credentials here: the module must import and tests must
        # collect with no GCP setup. Resolution happens on first synthesize.
        self._tokens: TokenSource = token_source or GoogleTokenSource(project=project)

    async def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {await self._tokens.token()}",
            # Attributes quota/billing to the caller's project, which ADC user
            # credentials (as opposed to a service account) otherwise lack.
            "x-goog-user-project": self._tokens.project(),
            "Content-Type": "application/json",
        }

    @staticmethod
    def _raise_for_status(status: int, data: bytes) -> None:
        """Raise the right provider error for a non-2xx Cloud TTS response."""
        if 200 <= status < 300:
            return
        snippet = data[:200].decode("utf-8", "replace").strip()
        message = f"google tts http {status}: {snippet}"
        if classify_http_status(status) == "retryable":
            raise RetryableProviderError(message)
        raise TerminalProviderError(message)

    async def _post(self, body: dict) -> dict:
        headers = await self._headers()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(_ENDPOINT, json=body, headers=headers) as resp:
                    data = await resp.read()
                    self._raise_for_status(resp.status, data)
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"google tts network failure: {exc}") from exc
        try:
            return json.loads(data)
        except ValueError as exc:
            raise TerminalProviderError(f"google tts returned non-JSON body: {exc}") from exc

    async def synthesize(
        self, text: str, voice_id: str, emotion: str | None, params: dict
    ) -> TTSResult:
        body = build_request(text, voice_id, emotion, params)
        payload = await self._post(body)

        encoded = payload.get("audioContent")
        if not encoded:
            raise TerminalProviderError(f"google tts returned no audioContent: {payload}")
        try:
            wav = base64.b64decode(encoded)
        except (ValueError, TypeError) as exc:
            raise TerminalProviderError(f"google tts audioContent is not base64: {exc}") from exc

        try:
            samples, sr = _decode_wav(wav)
        except (sf.LibsndfileError, RuntimeError) as exc:
            raise TerminalProviderError(f"undecodable google tts audio: {exc}") from exc

        return TTSResult(
            audio_bytes=wav,
            duration_ms=round(len(samples) / sr * 1000),
            cost_cents=self.estimate_cost_cents(text),
            provider=self.name,
            model=body["voice"]["modelName"],
            gen_params={
                "voice_id": voice_id,
                "emotion": emotion,
                "style_prompt": body["input"].get("prompt"),
                "language_code": body["voice"]["languageCode"],
                "format": "wav",
                "sample_rate": sr,
                **params,
            },
        )

    async def list_voices(self) -> list[Voice]:
        # Static/curated (no network). A live catalog would GET
        # https://texttospeech.googleapis.com/v1/voices, which returns every
        # voice for the project including the Chirp 3 and Studio families.
        return list(_CURATED_VOICES)

    def estimate_cost_cents(self, text: str) -> int:
        # Gemini-TTS bills tokens, not characters, so this is a cost-governor
        # estimate and NOT billing-accurate. For gemini-2.5-flash-tts at
        # $0.50/1M input text tokens and $10.00/1M output audio tokens: ~4 chars
        # per text token is negligible, and speech runs ~12.5 chars/second
        # against ~25 audio tokens/second, i.e. ~2 audio tokens per character
        # => ~0.002 cents/char, which dominates. Floor of 1 cent so a request is
        # never free in the cost governor's ledger.
        return max(1, ceil(len(text) * 0.002))
