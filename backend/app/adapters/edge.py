"""EdgeTTSAdapter: free Microsoft neural voices via edge-tts (no API key).

edge-tts only emits MP3. libsndfile >= 1.1 decodes MP3, so we decode to PCM in
memory and store canonical WAV bytes in TTSResult.audio_bytes. On platforms
whose libsndfile lacks MP3 support we fall back to storing the raw MP3 bytes
with a format tag in gen_params (duration then estimated from word count).
"""

from __future__ import annotations

import asyncio
import io

import aiohttp
import edge_tts
import numpy as np
import soundfile as sf

from app.adapters.base import RetryableProviderError, TerminalProviderError, TTSResult, Voice
from app.nlp.emotion import EMOTIONS

# Canonical emotion -> edge-tts prosody overrides (rate/pitch/volume deltas).
# edge-tts has no native "emotion" control, so delivery is approximated by
# nudging speaking rate, pitch, and loudness. Tuned by ear, not science;
# adjust freely as long as the direction (e.g. whispering is quieter/slower)
# stays sensible. Keys must cover exactly app.nlp.emotion.EMOTIONS.
_EMOTION_PROSODY: dict[str, dict[str, str]] = {
    "angry": {"rate": "+8%", "pitch": "+8Hz", "volume": "+12%"},
    "shouting": {"rate": "+6%", "pitch": "+12Hz", "volume": "+25%"},
    "whispering": {"rate": "-12%", "pitch": "-6Hz", "volume": "-30%"},
    "sad": {"rate": "-12%", "pitch": "-10Hz", "volume": "-6%"},
    "afraid": {"rate": "+6%", "pitch": "+12Hz", "volume": "-4%"},
    "excited": {"rate": "+14%", "pitch": "+14Hz", "volume": "+12%"},
    "happy": {"rate": "+6%", "pitch": "+8Hz", "volume": "+6%"},
    "calm": {"rate": "-8%", "pitch": "-4Hz", "volume": "-6%"},
    "urgent": {"rate": "+16%", "pitch": "+6Hz", "volume": "+8%"},
    "sarcastic": {"rate": "-6%", "pitch": "-4Hz", "volume": "+0%"},
    "surprised": {"rate": "+10%", "pitch": "+16Hz", "volume": "+10%"},
    "serious": {"rate": "-4%", "pitch": "-6Hz", "volume": "+2%"},
}
assert set(_EMOTION_PROSODY) == set(EMOTIONS), "prosody table drifted from EMOTIONS vocabulary"


def prosody_for(emotion: str | None) -> dict[str, str]:
    """Map a canonical emotion to edge-tts ``rate``/``pitch``/``volume`` overrides.

    Pure and importable without any network access. ``None`` or an emotion
    outside the canonical vocabulary returns an empty dict, i.e. "use
    edge-tts's neutral defaults".
    """
    if emotion is None:
        return {}
    return dict(_EMOTION_PROSODY.get(emotion, {}))

_CURATED_VOICES: list[Voice] = [
    Voice(id="en-US-GuyNeural", name="Guy (US)", tags=["male", "narrator", "en-US"]),
    Voice(id="en-US-ChristopherNeural", name="Christopher (US)", tags=["male", "narrator", "en-US"]),
    Voice(id="en-US-JennyNeural", name="Jenny (US)", tags=["female", "conversational", "en-US"]),
    Voice(id="en-US-AriaNeural", name="Aria (US)", tags=["female", "expressive", "en-US"]),
    Voice(id="en-GB-RyanNeural", name="Ryan (UK)", tags=["male", "conversational", "en-GB"]),
    Voice(id="en-GB-SoniaNeural", name="Sonia (UK)", tags=["female", "conversational", "en-GB"]),
]

_NETWORK_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    ConnectionError,
    OSError,
)


def _mp3_supported() -> bool:
    return "MP3" in sf.available_formats()


def _decode_mp3(mp3_bytes: bytes) -> tuple[np.ndarray, int]:
    """Decode MP3 to mono float32 samples via libsndfile."""
    samples, sr = sf.read(io.BytesIO(mp3_bytes), dtype="float32", always_2d=True)
    return samples.mean(axis=1).astype(np.float32), int(sr)


def _pcm_to_wav_bytes(samples: np.ndarray, sr: int) -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, samples, sr, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


class EdgeTTSAdapter:
    name = "edge"

    async def synthesize(
        self, text: str, voice_id: str, emotion: str | None, params: dict
    ) -> TTSResult:
        # edge-tts has no native emotion control, so we map the requested
        # emotion to prosody (rate/pitch/volume) and use that as the default.
        # Explicit params always win over the emotion's defaults.
        defaults = prosody_for(emotion)
        communicate = edge_tts.Communicate(
            text,
            voice_id,
            rate=params.get("rate", defaults.get("rate", "+0%")),
            volume=params.get("volume", defaults.get("volume", "+0%")),
            pitch=params.get("pitch", defaults.get("pitch", "+0Hz")),
        )
        mp3 = bytearray()
        try:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    mp3.extend(chunk["data"])
        except _NETWORK_ERRORS as exc:
            raise RetryableProviderError(f"edge-tts network failure: {exc}") from exc
        if not mp3:
            raise RetryableProviderError("edge-tts returned no audio data")

        gen_params = {"voice_id": voice_id, "emotion": emotion, **params}
        if _mp3_supported():
            try:
                samples, sr = _decode_mp3(bytes(mp3))
            except (sf.LibsndfileError, RuntimeError) as exc:
                raise TerminalProviderError(f"undecodable edge-tts audio: {exc}") from exc
            return TTSResult(
                audio_bytes=_pcm_to_wav_bytes(samples, sr),
                duration_ms=round(len(samples) / sr * 1000),
                cost_cents=0,
                provider=self.name,
                model=voice_id,
                gen_params={**gen_params, "format": "wav", "sample_rate": sr},
            )

        # Raw-MP3 fallback: duration estimated (~150 wpm) since we can't decode.
        return TTSResult(
            audio_bytes=bytes(mp3),
            duration_ms=len(text.split()) * 400,
            cost_cents=0,
            provider=self.name,
            model=voice_id,
            gen_params={**gen_params, "format": "mp3"},
        )

    async def list_voices(self) -> list[Voice]:
        return list(_CURATED_VOICES)

    def estimate_cost_cents(self, text: str) -> int:
        return 0
