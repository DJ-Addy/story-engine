"""Unit tests for AzureTTSAdapter. No network, no real key.

The HTTP transport is mocked by monkeypatching ``azure_tts.aiohttp.ClientSession``
with a fake session/response pair that implements the async-context-manager
protocol. This exercises the real ``_post_ssml`` code path — SSML building,
status classification, and WAV decoding — without touching the network.
"""

import aiohttp
import numpy as np
import pytest

from app.adapters import azure_tts
from app.adapters.azure_tts import AzureTTSAdapter, build_ssml, style_for
from app.adapters.base import (
    RetryableProviderError,
    TerminalProviderError,
    TTSProvider,
    Voice,
)
from app.nlp.emotion import EMOTIONS
from app.render.audio.dsp import wav_bytes

SR = 24000
KEY = "test-key"
REGION = "eastus"


def _tone_wav_bytes(duration_s: float = 0.25) -> bytes:
    """A short 24kHz sine tone as real RIFF/WAV bytes (decodes to a duration)."""
    t = np.arange(int(duration_s * SR), dtype=np.float32) / SR
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    return wav_bytes(tone, SR)


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

    async def text(self) -> str:
        return self._body.decode("utf-8", "replace")


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

    def post(self, url: str, data: bytes | None = None, headers: dict | None = None):
        type(self).calls.append({"url": url, "data": data, "headers": headers})
        if type(self).post_error is not None:
            raise type(self).post_error
        return _FakeResponse(type(self).status, type(self).body, type(self).read_error)


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSession]:
    _FakeSession.status = 200
    _FakeSession.body = _tone_wav_bytes(0.25)
    _FakeSession.post_error = None
    _FakeSession.read_error = None
    _FakeSession.calls = []
    monkeypatch.setattr(azure_tts.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


def _adapter() -> AzureTTSAdapter:
    return AzureTTSAdapter(subscription_key=KEY, region=REGION)


# --------------------------------------------------------------------------
# Protocol conformance
# --------------------------------------------------------------------------


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(_adapter(), TTSProvider)
    assert _adapter().name == "azure"


# --------------------------------------------------------------------------
# Pure SSML / style unit tests
# --------------------------------------------------------------------------


class TestStyleFor:
    @pytest.mark.parametrize("emotion", sorted(EMOTIONS))
    def test_every_canonical_emotion_maps_to_a_style(self, emotion: str) -> None:
        style = style_for(emotion)
        assert isinstance(style, str) and style

    def test_none_returns_none(self) -> None:
        assert style_for(None) is None

    def test_unknown_emotion_returns_none(self) -> None:
        assert style_for("bewildered") is None


class TestBuildSsml:
    def test_emotional_line_uses_express_as(self) -> None:
        ssml = build_ssml("I told you to stop.", "en-US-AriaNeural", "angry", {})
        assert "mstts:express-as" in ssml
        assert 'style="angry"' in ssml
        assert 'name="en-US-AriaNeural"' in ssml
        assert "<prosody" not in ssml

    def test_none_emotion_falls_back_to_prosody(self) -> None:
        ssml = build_ssml("Just talking.", "en-US-JennyNeural", None, {})
        assert "<prosody" in ssml
        assert "mstts:express-as" not in ssml

    def test_unknown_emotion_falls_back_to_prosody(self) -> None:
        ssml = build_ssml("Hmm.", "en-US-JennyNeural", "bewildered", {})
        assert "<prosody" in ssml
        assert "mstts:express-as" not in ssml

    def test_text_is_xml_escaped(self) -> None:
        ssml = build_ssml("Tom & Jerry < 5 > 3", "en-US-GuyNeural", "happy", {})
        assert "Tom &amp; Jerry &lt; 5 &gt; 3" in ssml
        assert "Tom & Jerry" not in ssml

    def test_explicit_params_override_prosody_defaults(self) -> None:
        ssml = build_ssml("Steady.", "en-US-GuyNeural", None, {"rate": "-20%", "pitch": "+5Hz"})
        assert 'rate="-20%"' in ssml
        assert 'pitch="+5Hz"' in ssml

    def test_namespaces_present(self) -> None:
        ssml = build_ssml("Hi", "en-US-JennyNeural", "sad", {})
        assert 'xmlns="http://www.w3.org/2001/10/synthesis"' in ssml
        assert 'xmlns:mstts="https://www.w3.org/2001/mstts"' in ssml


# --------------------------------------------------------------------------
# Success path (mocked HTTP)
# --------------------------------------------------------------------------


async def test_synthesize_success_decodes_duration(fake_session: type[_FakeSession]) -> None:
    result = await _adapter().synthesize("I told you to stop.", "en-US-AriaNeural", "angry", {})

    assert result.provider == "azure"
    assert result.model == "en-US-AriaNeural"
    assert result.audio_bytes[:4] == b"RIFF"
    assert result.gen_params["style"] == "angry"
    assert result.gen_params["emotion"] == "angry"
    assert result.gen_params["format"] == "wav"
    assert result.gen_params["sample_rate"] == SR
    assert result.duration_ms > 0
    assert abs(result.duration_ms - 250) < 50
    assert result.cost_cents >= 1


async def test_synthesize_posts_ssml_to_regional_endpoint(
    fake_session: type[_FakeSession],
) -> None:
    await _adapter().synthesize("Hello & goodbye", "en-US-JennyNeural", "happy", {})
    call = fake_session.calls[-1]
    assert call["url"] == f"https://{REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
    assert call["headers"]["Ocp-Apim-Subscription-Key"] == KEY
    assert call["headers"]["Content-Type"] == "application/ssml+xml"
    assert call["headers"]["X-Microsoft-OutputFormat"] == "riff-24khz-16bit-mono-pcm"
    # Body is the escaped SSML, utf-8 encoded, using the cheerful express-as style.
    sent = call["data"].decode("utf-8")
    assert 'style="cheerful"' in sent
    assert "Hello &amp; goodbye" in sent


async def test_none_emotion_records_null_style(fake_session: type[_FakeSession]) -> None:
    result = await _adapter().synthesize("Neutral line.", "en-US-GuyNeural", None, {})
    assert result.gen_params["style"] is None
    assert result.gen_params["emotion"] is None


# --------------------------------------------------------------------------
# Error paths (mocked HTTP)
# --------------------------------------------------------------------------


async def test_http_429_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.status = 429
    fake_session.body = b"Too Many Requests"
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "en-US-JennyNeural", "angry", {})


async def test_http_500_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.status = 500
    fake_session.body = b"Internal Server Error"
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "en-US-JennyNeural", "angry", {})


async def test_http_400_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.status = 400
    fake_session.body = b"Bad Request: invalid SSML"
    with pytest.raises(TerminalProviderError):
        await _adapter().synthesize("Hi", "en-US-JennyNeural", "angry", {})


async def test_network_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.post_error = aiohttp.ClientError("connection reset")
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "en-US-JennyNeural", "angry", {})


async def test_read_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.read_error = aiohttp.ClientError("stream interrupted")
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "en-US-JennyNeural", "angry", {})


# --------------------------------------------------------------------------
# Missing credentials (no network at all)
# --------------------------------------------------------------------------


async def test_missing_credentials_raises_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)

    # Any network use would blow up loudly rather than silently pass.
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network should be attempted without credentials")

    monkeypatch.setattr(azure_tts.aiohttp, "ClientSession", _boom)

    adapter = AzureTTSAdapter(subscription_key=None, region=None)
    with pytest.raises((TerminalProviderError, ValueError)):
        await adapter.synthesize("Hi", "en-US-JennyNeural", "angry", {})


def test_construction_never_raises_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.delenv("AZURE_SPEECH_REGION", raising=False)
    # Must import/construct fine so offline collection works.
    assert AzureTTSAdapter().name == "azure"


# --------------------------------------------------------------------------
# Curated voices + cost
# --------------------------------------------------------------------------


async def test_list_voices_curated_no_network() -> None:
    voices = await AzureTTSAdapter().list_voices()
    ids = {voice.id for voice in voices}
    assert {
        "en-US-GuyNeural",
        "en-US-ChristopherNeural",
        "en-US-JennyNeural",
        "en-US-AriaNeural",
        "en-GB-RyanNeural",
        "en-GB-SoniaNeural",
    } <= ids
    assert all(isinstance(voice, Voice) and voice.tags for voice in voices)


def test_estimate_cost_scales_with_length() -> None:
    adapter = AzureTTSAdapter()
    assert adapter.estimate_cost_cents("") == 1  # floor of 1
    assert adapter.estimate_cost_cents("x") == 1
    long_text = "a" * 10_000
    assert adapter.estimate_cost_cents(long_text) == 16  # 10000 * 0.0016
