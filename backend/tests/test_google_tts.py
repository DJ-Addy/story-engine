"""Unit tests for GoogleTTSAdapter. No network, no credentials, no credits.

The HTTP transport is mocked by monkeypatching ``google_tts.aiohttp.ClientSession``
with a fake session/response pair implementing the async-context-manager protocol
(the same approach as the retired ``test_azure_tts`` / ``test_elevenlabs``), and
the ADC bearer token comes from a stub ``TokenSource`` so ``google-auth`` is never
touched. This exercises the real ``_post`` path — request shaping, auth headers,
status classification, base64 + WAV decoding — without touching the network.
"""

import json

import aiohttp
import numpy as np
import pytest

from app.adapters import google_tts
from app.adapters.base import (
    RetryableProviderError,
    TerminalProviderError,
    TTSProvider,
    TTSResult,
    Voice,
)
from app.adapters.google_tts import (
    GoogleTTSAdapter,
    build_request,
    style_prompt_for,
)
from app.api.deps import get_tts
from app.nlp.emotion import EMOTIONS
from app.render.audio.dsp import wav_bytes

SR = 24000
TOKEN = "test-token"
PROJECT = "test-project"
ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"
DEFAULT_MODEL = "gemini-2.5-flash-tts"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter defaults must not depend on the developer's environment."""
    for var in (
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_TTS_MODEL",
        "GOOGLE_TTS_LANGUAGE_CODE",
    ):
        monkeypatch.delenv(var, raising=False)


def _tone_wav_bytes(duration_s: float = 0.25) -> bytes:
    """A short 24kHz sine tone as real RIFF/WAV bytes (what LINEAR16 returns)."""
    t = np.arange(int(duration_s * SR), dtype=np.float32) / SR
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    return wav_bytes(tone, SR)


def _audio_response(wav: bytes | None = None) -> dict:
    import base64

    return {"audioContent": base64.b64encode(wav or _tone_wav_bytes()).decode("ascii")}


# --------------------------------------------------------------------------
# Fake transport + token source
# --------------------------------------------------------------------------


class _StubTokens:
    """A TokenSource that mints a canned token without google-auth."""

    def __init__(
        self,
        token: str = TOKEN,
        project: str = PROJECT,
        error: Exception | None = None,
    ) -> None:
        self._token = token
        self._project = project
        self._error = error

    async def token(self) -> str:
        if self._error is not None:
            raise self._error
        return self._token

    def project(self) -> str:
        if self._error is not None:
            raise self._error
        return self._project


class _FakeResponse:
    def __init__(self, status: int, body: object, read_error: Exception | None = None) -> None:
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
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
    """Stands in for aiohttp.ClientSession; records posts, returns canned JSON."""

    status: int = 200
    body: object = {}
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
    _FakeSession.body = _audio_response()
    _FakeSession.post_error = None
    _FakeSession.read_error = None
    _FakeSession.calls = []
    monkeypatch.setattr(google_tts.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


def _adapter(**kwargs: object) -> GoogleTTSAdapter:
    return GoogleTTSAdapter(token_source=_StubTokens(**kwargs))  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Protocol conformance + construction
# --------------------------------------------------------------------------


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(_adapter(), TTSProvider)
    assert _adapter().name == "google-tts"


def test_construction_never_raises_without_credentials() -> None:
    # Must import/construct fine so offline collection works.
    assert GoogleTTSAdapter().name == "google-tts"
    assert GoogleTTSAdapter(project="p").name == "google-tts"


# --------------------------------------------------------------------------
# Pure style-prompt unit tests
# --------------------------------------------------------------------------


class TestStylePromptFor:
    @pytest.mark.parametrize("emotion", sorted(EMOTIONS))
    def test_every_canonical_emotion_maps_to_an_instruction(self, emotion: str) -> None:
        prompt = style_prompt_for(emotion)
        assert isinstance(prompt, str) and prompt

    def test_instructions_are_distinct_per_emotion(self) -> None:
        prompts = {style_prompt_for(emotion) for emotion in EMOTIONS}
        assert len(prompts) == len(EMOTIONS)

    def test_none_returns_none(self) -> None:
        assert style_prompt_for(None) is None

    def test_unknown_emotion_returns_none(self) -> None:
        assert style_prompt_for("bewildered") is None


# --------------------------------------------------------------------------
# Pure request-building unit tests
# --------------------------------------------------------------------------


class TestBuildRequest:
    def test_emotional_line_carries_a_style_prompt(self) -> None:
        body = build_request("I told you to stop.", "Kore", "angry", {})
        assert body == {
            "input": {
                "text": "I told you to stop.",
                "prompt": style_prompt_for("angry"),
            },
            "voice": {
                "languageCode": "en-US",
                "name": "Kore",
                "modelName": DEFAULT_MODEL,
            },
            "audioConfig": {"audioEncoding": "LINEAR16"},
        }

    def test_none_emotion_sends_no_prompt(self) -> None:
        body = build_request("Just talking.", "Charon", None, {})
        assert body["input"] == {"text": "Just talking."}

    def test_unknown_emotion_sends_no_prompt(self) -> None:
        body = build_request("Hmm.", "Charon", "bewildered", {})
        assert "prompt" not in body["input"]

    def test_explicit_prompt_param_wins_over_emotion(self) -> None:
        body = build_request("Line.", "Puck", "angry", {"prompt": "Read it like a lullaby."})
        assert body["input"]["prompt"] == "Read it like a lullaby."

    def test_explicit_empty_prompt_suppresses_the_emotion_style(self) -> None:
        body = build_request("Line.", "Puck", "angry", {"prompt": ""})
        assert "prompt" not in body["input"]

    def test_params_override_language_and_model(self) -> None:
        body = build_request(
            "Bonjour.", "Aoede", None, {"language_code": "fr-FR", "model": "gemini-2.5-pro-tts"}
        )
        assert body["voice"]["languageCode"] == "fr-FR"
        assert body["voice"]["modelName"] == "gemini-2.5-pro-tts"

    def test_env_overrides_language_and_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_TTS_MODEL", "gemini-2.5-pro-tts")
        monkeypatch.setenv("GOOGLE_TTS_LANGUAGE_CODE", "en-GB")
        body = build_request("Hello.", "Leda", None, {})
        assert body["voice"]["modelName"] == "gemini-2.5-pro-tts"
        assert body["voice"]["languageCode"] == "en-GB"

    def test_params_beat_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_TTS_MODEL", "gemini-2.5-pro-tts")
        body = build_request("Hello.", "Leda", None, {"model": "gemini-2.5-flash-tts"})
        assert body["voice"]["modelName"] == "gemini-2.5-flash-tts"

    def test_audio_config_params_are_camel_cased(self) -> None:
        body = build_request(
            "Steady.",
            "Iapetus",
            None,
            {
                "speaking_rate": 0.9,
                "pitch": -2.0,
                "volume_gain_db": 3.0,
                "sample_rate_hertz": 16000,
            },
        )
        assert body["audioConfig"] == {
            "audioEncoding": "LINEAR16",
            "speakingRate": 0.9,
            "pitch": -2.0,
            "volumeGainDb": 3.0,
            "sampleRateHertz": 16000,
        }

    def test_unknown_params_are_not_forwarded(self) -> None:
        body = build_request("Hi.", "Kore", None, {"nonsense": 1})
        assert body["audioConfig"] == {"audioEncoding": "LINEAR16"}

    def test_oversize_text_is_terminal(self) -> None:
        with pytest.raises(TerminalProviderError):
            build_request("a" * 4001, "Kore", None, {})

    def test_limit_is_measured_in_bytes_not_characters(self) -> None:
        # Two bytes per char in UTF-8: 2000 chars fits exactly, 2001 does not.
        build_request("é" * 2000, "Kore", None, {})
        with pytest.raises(TerminalProviderError):
            build_request("é" * 2001, "Kore", None, {})


# --------------------------------------------------------------------------
# Success path (mocked HTTP)
# --------------------------------------------------------------------------


async def test_synthesize_success_decodes_duration(fake_session: type[_FakeSession]) -> None:
    result = await _adapter().synthesize("I told you to stop.", "Kore", "angry", {})

    assert isinstance(result, TTSResult)
    assert result.provider == "google-tts"
    assert result.model == DEFAULT_MODEL
    assert result.audio_bytes[:4] == b"RIFF"
    assert result.gen_params["voice_id"] == "Kore"
    assert result.gen_params["emotion"] == "angry"
    assert result.gen_params["style_prompt"] == style_prompt_for("angry")
    assert result.gen_params["language_code"] == "en-US"
    assert result.gen_params["format"] == "wav"
    assert result.gen_params["sample_rate"] == SR
    assert result.duration_ms == 250
    assert result.cost_cents >= 1


async def test_synthesize_posts_to_the_synthesize_endpoint(
    fake_session: type[_FakeSession],
) -> None:
    await _adapter().synthesize("Hello there", "Charon", "happy", {})
    call = fake_session.calls[-1]

    assert call["url"] == ENDPOINT
    assert call["headers"] == {
        "Authorization": f"Bearer {TOKEN}",
        "x-goog-user-project": PROJECT,
        "Content-Type": "application/json",
    }
    assert call["json"] == build_request("Hello there", "Charon", "happy", {})
    assert call["json"]["input"]["prompt"] == style_prompt_for("happy")


async def test_none_emotion_records_null_style(fake_session: type[_FakeSession]) -> None:
    result = await _adapter().synthesize("Neutral line.", "Iapetus", None, {})
    assert result.gen_params["emotion"] is None
    assert result.gen_params["style_prompt"] is None
    assert "prompt" not in fake_session.calls[-1]["json"]["input"]


async def test_model_param_is_reflected_in_the_result(
    fake_session: type[_FakeSession],
) -> None:
    result = await _adapter().synthesize(
        "Fast line.", "Puck", None, {"model": "gemini-2.5-pro-tts"}
    )
    assert result.model == "gemini-2.5-pro-tts"
    assert fake_session.calls[-1]["json"]["voice"]["modelName"] == "gemini-2.5-pro-tts"
    assert result.gen_params["model"] == "gemini-2.5-pro-tts"


async def test_oversize_text_never_reaches_the_network(
    fake_session: type[_FakeSession],
) -> None:
    with pytest.raises(TerminalProviderError):
        await _adapter().synthesize("a" * 5000, "Kore", None, {})
    assert fake_session.calls == []


# --------------------------------------------------------------------------
# Error paths (mocked HTTP)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_retryable_http_statuses(
    fake_session: type[_FakeSession], status: int
) -> None:
    fake_session.status = status
    fake_session.body = b"Server is sad"
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "Kore", "angry", {})


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_terminal_http_statuses(
    fake_session: type[_FakeSession], status: int
) -> None:
    fake_session.status = status
    fake_session.body = b"Bad Request: invalid voice"
    with pytest.raises(TerminalProviderError):
        await _adapter().synthesize("Hi", "Kore", "angry", {})


async def test_network_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.post_error = aiohttp.ClientError("connection reset")
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "Kore", "angry", {})


async def test_read_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.read_error = aiohttp.ClientError("stream interrupted")
    with pytest.raises(RetryableProviderError):
        await _adapter().synthesize("Hi", "Kore", "angry", {})


async def test_non_json_body_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = b"<html>not json</html>"
    with pytest.raises(TerminalProviderError):
        await _adapter().synthesize("Hi", "Kore", None, {})


async def test_missing_audio_content_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = {"audioContent": ""}
    with pytest.raises(TerminalProviderError):
        await _adapter().synthesize("Hi", "Kore", None, {})


async def test_non_base64_audio_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = {"audioContent": "A"}  # invalid base64 length
    with pytest.raises(TerminalProviderError):
        await _adapter().synthesize("Hi", "Kore", None, {})


async def test_undecodable_audio_is_terminal(fake_session: type[_FakeSession]) -> None:
    import base64

    fake_session.body = {"audioContent": base64.b64encode(b"not-a-wav-file").decode("ascii")}
    with pytest.raises(TerminalProviderError):
        await _adapter().synthesize("Hi", "Kore", None, {})


# --------------------------------------------------------------------------
# Missing credentials (no network at all)
# --------------------------------------------------------------------------


async def test_credential_failure_is_terminal_without_network(
    fake_session: type[_FakeSession],
) -> None:
    adapter = GoogleTTSAdapter(
        token_source=_StubTokens(error=TerminalProviderError("no ADC"))  # type: ignore[arg-type]
    )
    with pytest.raises(TerminalProviderError):
        await adapter.synthesize("Hi", "Kore", "angry", {})
    assert fake_session.calls == []


async def test_missing_credentials_raises_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    # google-auth cannot resolve => terminal, and no HTTP is attempted at all.
    monkeypatch.setitem(sys.modules, "google", None)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network should be attempted without credentials")

    monkeypatch.setattr(google_tts.aiohttp, "ClientSession", _boom)

    with pytest.raises(TerminalProviderError):
        await GoogleTTSAdapter().synthesize("Hi", "Kore", "angry", {})


# --------------------------------------------------------------------------
# Curated voices + cost
# --------------------------------------------------------------------------


async def test_list_voices_curated_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("list_voices must not hit the network")

    monkeypatch.setattr(google_tts.aiohttp, "ClientSession", _boom)

    voices = await GoogleTTSAdapter().list_voices()
    ids = {voice.id for voice in voices}
    assert {
        "Charon",
        "Iapetus",
        "Puck",
        "Enceladus",
        "Kore",
        "Aoede",
        "Leda",
        "Callirrhoe",
    } <= ids
    assert all(isinstance(voice, Voice) and voice.tags for voice in voices)
    # Both genders are castable.
    assert any("male" in voice.tags for voice in voices)
    assert any("female" in voice.tags for voice in voices)


def test_estimate_cost_scales_with_length() -> None:
    adapter = GoogleTTSAdapter()
    assert adapter.estimate_cost_cents("") == 1  # floor of 1
    assert adapter.estimate_cost_cents("x") == 1
    assert adapter.estimate_cost_cents("a" * 10_000) == 20  # 10000 * 0.002


# --------------------------------------------------------------------------
# Provider selection via get_tts
# --------------------------------------------------------------------------


def test_get_tts_selects_google_when_project_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    assert type(get_tts()).__name__ == "GoogleTTSAdapter"


def test_get_tts_raises_when_project_unset() -> None:
    with pytest.raises(TerminalProviderError):
        get_tts()
