"""Unit tests for EdgeTTSAdapter. No network: Communicate is monkeypatched."""

import io

import aiohttp
import numpy as np
import pytest
import soundfile as sf

from app.adapters import edge
from app.adapters.base import RetryableProviderError, TTSProvider, Voice
from app.adapters.edge import EdgeTTSAdapter, prosody_for
from app.nlp.emotion import EMOTIONS

SR = 24000


def _tone_mp3_bytes(duration_s: float = 0.25) -> bytes:
    """Encode a short sine tone as MP3 in memory via libsndfile."""
    if "MP3" not in sf.available_formats():
        pytest.skip("libsndfile lacks MP3 support on this platform")
    t = np.arange(int(duration_s * SR), dtype=np.float32) / SR
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    buffer = io.BytesIO()
    sf.write(buffer, tone, SR, format="MP3")
    return buffer.getvalue()


class _FakeCommunicate:
    """Stands in for edge_tts.Communicate; streams canned bytes or raises."""

    payload: bytes = b""
    error: Exception | None = None

    calls: list[dict[str, str]] = []

    def __init__(self, text: str, voice: str, **kwargs: str) -> None:
        self.text = text
        self.voice = voice
        type(self).calls.append(kwargs)

    async def stream(self):
        if self.error is not None:
            raise self.error
        half = len(self.payload) // 2
        yield {"type": "audio", "data": self.payload[:half]}
        yield {"type": "WordBoundary", "offset": 0, "duration": 0, "text": self.text}
        yield {"type": "audio", "data": self.payload[half:]}


@pytest.fixture
def fake_communicate(monkeypatch: pytest.MonkeyPatch) -> type[_FakeCommunicate]:
    _FakeCommunicate.payload = b""
    _FakeCommunicate.error = None
    _FakeCommunicate.calls = []
    monkeypatch.setattr(edge.edge_tts, "Communicate", _FakeCommunicate)
    return _FakeCommunicate


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(EdgeTTSAdapter(), TTSProvider)
    assert EdgeTTSAdapter().name == "edge"


async def test_synthesize_decodes_mp3_to_wav(fake_communicate: type[_FakeCommunicate]) -> None:
    fake_communicate.payload = _tone_mp3_bytes(0.25)
    result = await EdgeTTSAdapter().synthesize("Hello there", "en-US-JennyNeural", None, {})

    assert result.provider == "edge"
    assert result.model == "en-US-JennyNeural"
    assert result.cost_cents == 0
    assert result.gen_params["format"] == "wav"
    assert result.audio_bytes[:4] == b"RIFF"

    samples, sr = sf.read(io.BytesIO(result.audio_bytes), dtype="float32")
    # MP3 codecs pad edges; duration should still be within ~100 ms of the tone.
    assert result.duration_ms == round(len(samples) / sr * 1000)
    assert abs(result.duration_ms - 250) < 100


async def test_synthesize_raw_fallback_without_mp3_decode(
    fake_communicate: type[_FakeCommunicate], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_communicate.payload = b"\xff\xfbfake-mp3-frames"
    monkeypatch.setattr(edge, "_mp3_supported", lambda: False)
    result = await EdgeTTSAdapter().synthesize("one two three", "en-US-GuyNeural", None, {})

    assert result.audio_bytes == fake_communicate.payload
    assert result.gen_params["format"] == "mp3"
    assert result.duration_ms > 0


async def test_network_error_maps_to_retryable(
    fake_communicate: type[_FakeCommunicate],
) -> None:
    fake_communicate.error = aiohttp.ClientError("connection reset")
    with pytest.raises(RetryableProviderError):
        await EdgeTTSAdapter().synthesize("Hi", "en-US-JennyNeural", None, {})


async def test_empty_audio_is_retryable(fake_communicate: type[_FakeCommunicate]) -> None:
    fake_communicate.payload = b""
    with pytest.raises(RetryableProviderError):
        await EdgeTTSAdapter().synthesize("Hi", "en-US-JennyNeural", None, {})


async def test_list_voices_curated() -> None:
    voices = await EdgeTTSAdapter().list_voices()
    ids = {voice.id for voice in voices}
    assert {
        "en-US-GuyNeural",
        "en-US-JennyNeural",
        "en-US-AriaNeural",
        "en-GB-RyanNeural",
        "en-GB-SoniaNeural",
        "en-US-ChristopherNeural",
    } <= ids
    assert all(isinstance(voice, Voice) and voice.tags for voice in voices)


def test_estimate_cost_is_zero() -> None:
    assert EdgeTTSAdapter().estimate_cost_cents("any text at all") == 0


class TestProsodyFor:
    @pytest.mark.parametrize("emotion", sorted(EMOTIONS))
    def test_known_emotion_returns_full_prosody(self, emotion: str) -> None:
        prosody = prosody_for(emotion)
        assert set(prosody) == {"rate", "pitch", "volume"}
        assert all(isinstance(value, str) and value for value in prosody.values())

    def test_none_returns_neutral_empty_dict(self) -> None:
        assert prosody_for(None) == {}

    def test_unknown_emotion_returns_neutral_empty_dict(self) -> None:
        assert prosody_for("bewildered") == {}

    def test_result_is_a_fresh_copy(self) -> None:
        # Callers must not be able to mutate the shared prosody table.
        prosody = prosody_for("angry")
        prosody["rate"] = "mutated"
        assert prosody_for("angry")["rate"] != "mutated"


async def test_synthesize_applies_emotion_prosody_by_default(
    fake_communicate: type[_FakeCommunicate],
) -> None:
    fake_communicate.payload = _tone_mp3_bytes(0.25)
    await EdgeTTSAdapter().synthesize(
        "I can't believe it", "en-US-JennyNeural", "angry", {}
    )
    assert fake_communicate.calls[-1] == prosody_for("angry")


async def test_synthesize_explicit_params_override_emotion_prosody(
    fake_communicate: type[_FakeCommunicate],
) -> None:
    fake_communicate.payload = _tone_mp3_bytes(0.25)
    await EdgeTTSAdapter().synthesize(
        "Steady now", "en-US-JennyNeural", "angry", {"rate": "+0%"}
    )
    expected = prosody_for("angry")
    expected["rate"] = "+0%"
    assert fake_communicate.calls[-1] == expected


async def test_synthesize_without_emotion_uses_neutral_prosody(
    fake_communicate: type[_FakeCommunicate],
) -> None:
    fake_communicate.payload = _tone_mp3_bytes(0.25)
    await EdgeTTSAdapter().synthesize("Just talking.", "en-US-JennyNeural", None, {})
    assert fake_communicate.calls[-1] == {"rate": "+0%", "volume": "+0%", "pitch": "+0Hz"}


async def test_synthesize_records_emotion_in_gen_params(
    fake_communicate: type[_FakeCommunicate],
) -> None:
    fake_communicate.payload = _tone_mp3_bytes(0.25)
    result = await EdgeTTSAdapter().synthesize(
        "Get out.", "en-US-JennyNeural", "angry", {}
    )
    assert result.gen_params["emotion"] == "angry"
