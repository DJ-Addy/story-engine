"""Unit tests for ElevenLabsAdapter. No network, no real key.

The HTTP transport is mocked by monkeypatching ``elevenlabs.aiohttp.ClientSession``
with a fake session/response pair that implements the async-context-manager
protocol. This exercises the real ``_post_pcm`` code path — request shaping,
status classification, and PCM decoding — without touching the network.
"""

import aiohttp
import numpy as np
import pytest

from app.adapters import elevenlabs
from app.adapters.base import (
    RetryableProviderError,
    TerminalProviderError,
    TTSProvider,
    Voice,
)
from app.adapters.elevenlabs import (
    ElevenLabsAdapter,
    resolve_voice_id,
    voice_settings_for,
)
from app.api.deps import get_tts

SR = 24000
KEY = "test-key"


def _tone_pcm_bytes(duration_s: float = 0.25) -> bytes:
    """A short 24kHz sine tone as raw 16-bit LE mono PCM (what the API returns)."""
    t = np.arange(int(duration_s * SR), dtype=np.float32) / SR
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    return (tone * 32767).astype("<i2").tobytes()


class _FakeResponse:
    def __init__(self, status: int, body: bytes, read_error: Exception | None = None) -> None:
        self.status = status
        self._body = body
        self._read_error = read_error

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def read(self) -> bytes:
        if self._read_error is not None:
            raise self._read_error
        return self._body


class _FakeSession:
    """Stands in for aiohttp.ClientSession; records posts, returns canned bytes."""

    status: int = 200
    body: bytes = b""
    post_error: Exception | None = None
    read_error: Exception | None = None
    calls: list[dict] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        type(self).calls.append({"url": url, "json": json, "headers": headers})
        if type(self).post_error is not None:
            raise type(self).post_error
        return _FakeResponse(type(self).status, type(self).body, type(self).read_error)


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSession]:
    _FakeSession.status = 200
    _FakeSession.body = _tone_pcm_bytes(0.25)
    _FakeSession.post_error = None
    _FakeSession.read_error = None
    _FakeSession.calls = []
    monkeypatch.setattr(elevenlabs.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


def _adapter() -> ElevenLabsAdapter:
    return ElevenLabsAdapter(api_key=KEY)


# --------------------------------------------------------------------------
# Protocol conformance
# --------------------------------------------------------------------------


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(_adapter(), TTSProvider)
    assert _adapter().name == "elevenlabs"


def test_construction_never_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    # Must import/construct fine so offline collection works.
    assert ElevenLabsAdapter().name == "elevenlabs"


# --------------------------------------------------------------------------
# Pure voice_settings unit tests
# --------------------------------------------------------------------------


class TestVoiceSettingsFor:
    _KEYS = {"stability", "similarity_boost", "style", "use_speaker_boost"}

    @pytest.mark.parametrize("emotion", [None, "angry", "calm", "whispering", "happy", "bewildered"])
    def test_all_keys_always_present(self, emotion: str | None) -> None:
        settings = voice_settings_for(emotion)
        assert set(settings) == self._KEYS
        assert settings["similarity_boost"] == pytest.approx(0.75)
        assert settings["use_speaker_boost"] is True

    def test_high_arousal_vs_calm_differ(self) -> None:
        angry = voice_settings_for("angry")
        calm = voice_settings_for("calm")
        assert angry["stability"] < calm["stability"]
        assert angry["style"] > calm["style"]

    def test_none_is_neutral(self) -> None:
        settings = voice_settings_for(None)
        assert settings["stability"] == pytest.approx(0.5)
        assert settings["style"] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Pure voice resolution unit tests
# --------------------------------------------------------------------------


class TestResolveVoiceId:
    def test_edge_style_name_maps_into_curated_pool_and_is_stable(self) -> None:
        resolved = resolve_voice_id("en-US-JennyNeural")
        assert resolved in elevenlabs._CURATED_IDS
        assert resolved == resolve_voice_id("en-US-JennyNeural")

    def test_real_elevenlabs_id_passes_through(self) -> None:
        real = "21m00Tcm4TlvDq8ikWAM"
        assert resolve_voice_id(real) == real

    def test_empty_maps_into_curated_pool(self) -> None:
        assert resolve_voice_id("") in elevenlabs._CURATED_IDS

    def test_distinct_edge_names_can_map_to_distinct_voices(self) -> None:
        names = [
            "en-US-GuyNeural",
            "en-US-ChristopherNeural",
            "en-US-JennyNeural",
            "en-US-AriaNeural",
            "en-GB-RyanNeural",
            "en-GB-SoniaNeural",
        ]
        resolved = {resolve_voice_id(name) for name in names}
        assert len(resolved) >= 2


# --------------------------------------------------------------------------
# Success path (mocked HTTP)
# --------------------------------------------------------------------------


async def test_synthesize_success_decodes_duration(fake_session: type[_FakeSession]) -> None:
    result = await _adapter().synthesize("I told you to stop.", "en-US-AriaNeural", "angry", {})

    assert result.provider == "elevenlabs"
    assert result.model == "eleven_multilingual_v2"
    assert result.audio_bytes[:4] == b"RIFF"
    assert result.gen_params["emotion"] == "angry"
    assert result.gen_params["voice_settings"] == voice_settings_for("angry")
    assert result.gen_params["voice_id"] in elevenlabs._CURATED_IDS
    assert result.duration_ms > 0
    assert abs(result.duration_ms - 250) < 50
    assert result.cost_cents >= 1


async def test_synthesize_posts_json_to_voice_endpoint(fake_session: type[_FakeSession]) -> None:
    await _adapter().synthesize("Hello there", "21m00Tcm4TlvDq8ikWAM", "happy", {})
    call = fake_session.calls[-1]
    assert call["url"] == (
        "https://api.elevenlabs.io/v1/text-to-speech/"
        "21m00Tcm4TlvDq8ikWAM?output_format=pcm_24000"
    )
    assert call["headers"]["xi-api-key"] == KEY
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["Accept"] == "audio/pcm"
    body = call["json"]
    assert body["text"] == "Hello there"
    assert body["model_id"] == "eleven_multilingual_v2"
    assert body["voice_settings"] == voice_settings_for("happy")


async def test_synthesize_honors_model_id_param(fake_session: type[_FakeSession]) -> None:
    result = await _adapter().synthesize(
        "Fast line.", "21m00Tcm4TlvDq8ikWAM", None, {"model_id": "eleven_turbo_v2"}
    )
    assert result.model == "eleven_turbo_v2"
    assert fake_session.calls[-1]["json"]["model_id"] == "eleven_turbo_v2"


async def test_none_emotion_records_neutral_settings(fake_session: type[_FakeSession]) -> None:
    result = await _adapter().synthesize("Neutral line.", "pNInz6obpgDQGcFmaJgB", None, {})
    assert result.gen_params["emotion"] is None
    assert result.gen_params["voice_settings"] == voice_settings_for(None)


# --------------------------------------------------------------------------
# Error paths (mocked HTTP)
# --------------------------------------------------------------------------


async def test_http_429_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.status = 429
    fake_session.body = b"Too Many Requests"
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "21m00Tcm4TlvDq8ikWAM", "angry", {})


async def test_http_500_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.status = 500
    fake_session.body = b"Internal Server Error"
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "21m00Tcm4TlvDq8ikWAM", "angry", {})


async def test_http_400_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.status = 400
    fake_session.body = b"Bad Request: invalid voice settings"
    with pytest.raises(TerminalProviderError):
        await _adapter().synthesize("Hi", "21m00Tcm4TlvDq8ikWAM", "angry", {})


async def test_network_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.post_error = aiohttp.ClientError("connection reset")
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "21m00Tcm4TlvDq8ikWAM", "angry", {})


async def test_read_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.read_error = aiohttp.ClientError("stream interrupted")
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "21m00Tcm4TlvDq8ikWAM", "angry", {})


# --------------------------------------------------------------------------
# Missing key (no network at all)
# --------------------------------------------------------------------------


async def test_missing_key_raises_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    # Any network use would blow up loudly rather than silently pass.
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network should be attempted without a key")

    monkeypatch.setattr(elevenlabs.aiohttp, "ClientSession", _boom)

    adapter = ElevenLabsAdapter(api_key=None)
    with pytest.raises(TerminalProviderError):
        await adapter.synthesize("Hi", "21m00Tcm4TlvDq8ikWAM", "angry", {})


# --------------------------------------------------------------------------
# Curated voices + cost
# --------------------------------------------------------------------------


async def test_list_voices_curated_no_network() -> None:
    voices = await ElevenLabsAdapter().list_voices()
    ids = {voice.id for voice in voices}
    assert {
        "21m00Tcm4TlvDq8ikWAM",
        "pNInz6obpgDQGcFmaJgB",
        "ErXwobaYiN019PkySvjV",
        "EXAVITQu4vr4xnSDxMaL",
        "AZnzlk1XvdvUeBnXmlld",
    } <= ids
    assert all(isinstance(voice, Voice) and voice.tags for voice in voices)


def test_estimate_cost_scales_with_length() -> None:
    adapter = ElevenLabsAdapter()
    assert adapter.estimate_cost_cents("") == 1  # floor of 1
    assert adapter.estimate_cost_cents("x") == 1
    long_text = "a" * 1000
    assert adapter.estimate_cost_cents(long_text) == 30  # 1000 * 0.03


# --------------------------------------------------------------------------
# Provider auto-selection via get_tts
# --------------------------------------------------------------------------


def _clear_tts_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("ELEVENLABS_API_KEY", "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"):
        monkeypatch.delenv(var, raising=False)


def test_get_tts_selects_elevenlabs_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tts_env(monkeypatch)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-key")
    # Even with Azure creds also set, ElevenLabs wins on priority.
    monkeypatch.setenv("AZURE_SPEECH_KEY", "az-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    assert type(get_tts()).__name__ == "ElevenLabsAdapter"


def test_get_tts_selects_azure_when_only_azure_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tts_env(monkeypatch)
    monkeypatch.setenv("AZURE_SPEECH_KEY", "az-key")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "eastus")
    assert type(get_tts()).__name__ == "AzureTTSAdapter"


def test_get_tts_falls_back_to_edge_when_none_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_tts_env(monkeypatch)
    assert type(get_tts()).__name__ == "EdgeTTSAdapter"
