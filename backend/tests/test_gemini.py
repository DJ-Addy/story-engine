"""Unit tests for GeminiAdapter. No network, no credentials, no credits.

The HTTP transport is mocked by monkeypatching ``gemini.aiohttp.ClientSession``
with a fake session/response pair implementing the async-context-manager protocol
(the same approach as the retired ``test_elevenlabs`` / ``test_azure_tts``), and
the ADC bearer token comes from a stub ``TokenSource`` so ``google-auth`` is never
touched. This exercises the real ``complete`` path — URL construction, system
instruction mapping, status classification, candidate parsing and usage-based
pricing — without touching the network.
"""

import json

import aiohttp
import pytest

from app.adapters import gemini
from app.adapters.base import (
    LLMProvider,
    LLMResult,
    RetryableProviderError,
    TerminalProviderError,
)
from app.adapters.gemini import GeminiAdapter
from app.api.deps import get_llm

TOKEN = "test-token"
PROJECT = "test-project"
DEFAULT_MODEL = "gemini-3.8-flash"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter defaults must not depend on the developer's environment."""
    for var in (
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_GEMINI_MODEL",
        "GOOGLE_GEMINI_LOCATION",
    ):
        monkeypatch.delenv(var, raising=False)


def _completion(text: str = "Once upon a time.", **usage: int) -> dict:
    payload: dict = {
        "candidates": [
            {"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}
        ]
    }
    if usage:
        payload["usageMetadata"] = usage
    return payload


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
    _FakeSession.body = _completion()
    _FakeSession.post_error = None
    _FakeSession.read_error = None
    _FakeSession.calls = []
    monkeypatch.setattr(gemini.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


def _adapter(**kwargs: object) -> GeminiAdapter:
    return GeminiAdapter(token_source=_StubTokens(), **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Protocol conformance + construction
# --------------------------------------------------------------------------


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(_adapter(), LLMProvider)
    assert _adapter().name == "gemini"


def test_construction_never_raises_without_credentials() -> None:
    # Must import/construct fine so offline collection works.
    assert GeminiAdapter().name == "gemini"
    assert GeminiAdapter(project="p", location="us-central1", model="m").model == "m"


class TestModelSelection:
    def test_default_model(self) -> None:
        assert _adapter().model == DEFAULT_MODEL

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_GEMINI_MODEL", "gemini-3.8-pro")
        assert GeminiAdapter().model == "gemini-3.8-pro"

    def test_constructor_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_GEMINI_MODEL", "gemini-3.8-pro")
        assert GeminiAdapter(model="gemini-3.8-flash").model == "gemini-3.8-flash"


# --------------------------------------------------------------------------
# Endpoint construction
# --------------------------------------------------------------------------


class TestUrl:
    def test_global_endpoint_has_no_region_prefix(self) -> None:
        url = _adapter()._url(DEFAULT_MODEL)
        assert url == (
            "https://aiplatform.googleapis.com/v1/projects/test-project"
            "/locations/global/publishers/google/models/gemini-3.8-flash:generateContent"
        )

    def test_regional_endpoint_is_host_prefixed(self) -> None:
        url = _adapter(location="us-central1")._url(DEFAULT_MODEL)
        assert url == (
            "https://us-central1-aiplatform.googleapis.com/v1/projects/test-project"
            "/locations/us-central1/publishers/google/models/gemini-3.8-flash:generateContent"
        )

    def test_env_selects_location(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_GEMINI_LOCATION", "europe-west4")
        adapter = GeminiAdapter(token_source=_StubTokens())  # type: ignore[arg-type]
        assert "europe-west4-aiplatform.googleapis.com" in adapter._url(DEFAULT_MODEL)


# --------------------------------------------------------------------------
# Success path (mocked HTTP)
# --------------------------------------------------------------------------


async def test_complete_returns_llm_result(fake_session: type[_FakeSession]) -> None:
    result = await _adapter().complete("You are a script doctor.", "Rewrite this beat.", {})

    assert isinstance(result, LLMResult)
    assert result.text == "Once upon a time."
    assert result.provider == "gemini"
    assert result.model == DEFAULT_MODEL
    assert result.cost_cents >= 1


async def test_request_shape_and_auth_header(fake_session: type[_FakeSession]) -> None:
    await _adapter().complete("SYSTEM", "USER", {})
    call = fake_session.calls[-1]

    assert call["url"].endswith(
        "/publishers/google/models/gemini-3.8-flash:generateContent"
    )
    assert call["headers"] == {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    assert call["json"] == {
        "contents": [{"role": "user", "parts": [{"text": "USER"}]}],
        "systemInstruction": {"parts": [{"text": "SYSTEM"}]},
    }


async def test_empty_system_prompt_is_omitted(fake_session: type[_FakeSession]) -> None:
    await _adapter().complete("", "USER", {})
    assert "systemInstruction" not in fake_session.calls[-1]["json"]


async def test_params_become_generation_config(fake_session: type[_FakeSession]) -> None:
    await _adapter().complete(
        "s",
        "u",
        {"temperature": 0.2, "maxOutputTokens": 512, "responseMimeType": "application/json"},
    )
    assert fake_session.calls[-1]["json"]["generationConfig"] == {
        "temperature": 0.2,
        "maxOutputTokens": 512,
        "responseMimeType": "application/json",
    }


async def test_model_param_overrides_and_is_not_leaked_into_config(
    fake_session: type[_FakeSession],
) -> None:
    result = await _adapter().complete("s", "u", {"model": "gemini-3.8-pro", "temperature": 0.1})
    body = fake_session.calls[-1]["json"]

    assert result.model == "gemini-3.8-pro"
    assert "gemini-3.8-pro:generateContent" in fake_session.calls[-1]["url"]
    # ``model`` is a routing key, not a generationConfig field.
    assert body["generationConfig"] == {"temperature": 0.1}


async def test_caller_params_are_not_mutated(fake_session: type[_FakeSession]) -> None:
    params = {"model": "gemini-3.8-pro", "temperature": 0.1}
    await _adapter().complete("s", "u", params)
    assert params == {"model": "gemini-3.8-pro", "temperature": 0.1}


async def test_multiple_text_parts_are_concatenated(
    fake_session: type[_FakeSession],
) -> None:
    fake_session.body = {
        "candidates": [
            {
                "content": {"parts": [{"text": "INT. "}, {"text": "CABIN"}, {"inlineData": {}}]},
                "finishReason": "STOP",
            }
        ]
    }
    result = await _adapter().complete("s", "u", {})
    assert result.text == "INT. CABIN"


# --------------------------------------------------------------------------
# Cost accounting
# --------------------------------------------------------------------------


async def test_cost_uses_reported_token_usage(fake_session: type[_FakeSession]) -> None:
    fake_session.body = _completion(promptTokenCount=100_000, candidatesTokenCount=50_000)
    result = await _adapter().complete("s", "u", {})
    # 100000 * 7.5e-5 + 50000 * 3.75e-4 = 26.25 cents, rounded up.
    assert result.cost_cents == 27


async def test_cost_falls_back_to_the_estimate_without_usage(
    fake_session: type[_FakeSession],
) -> None:
    fake_session.body = _completion()  # no usageMetadata
    adapter = _adapter()
    system, user = "s" * 40_000, "u" * 60_000
    result = await adapter.complete(system, user, {})
    assert result.cost_cents == adapter.estimate_cost_cents(len(system) + len(user))


async def test_partial_usage_falls_back_to_the_estimate(
    fake_session: type[_FakeSession],
) -> None:
    fake_session.body = _completion(promptTokenCount=100_000)  # no candidatesTokenCount
    adapter = _adapter()
    result = await adapter.complete("s", "u", {})
    assert result.cost_cents == adapter.estimate_cost_cents(2)


def test_estimate_cost_scales_with_prompt_size() -> None:
    adapter = GeminiAdapter()
    assert adapter.estimate_cost_cents(0) == 1  # floor of 1
    assert adapter.estimate_cost_cents(100) == 1
    # 100000 chars => 25000 tokens => 25000 * (7.5e-5 + 0.5 * 3.75e-4) = 6.5625
    assert adapter.estimate_cost_cents(100_000) == 7
    assert adapter.estimate_cost_cents(1_000_000) == 66


# --------------------------------------------------------------------------
# Error paths (mocked HTTP)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_retryable_http_statuses(fake_session: type[_FakeSession], status: int) -> None:
    fake_session.status = status
    fake_session.body = b"Server is sad"
    with pytest.raises(RetryableProviderError):
        await _adapter().complete("s", "u", {})


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_terminal_http_statuses(fake_session: type[_FakeSession], status: int) -> None:
    fake_session.status = status
    fake_session.body = b"Bad Request: unknown model"
    with pytest.raises(TerminalProviderError):
        await _adapter().complete("s", "u", {})


async def test_network_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.post_error = aiohttp.ClientError("connection reset")
    with pytest.raises(RetryableProviderError):
        await _adapter().complete("s", "u", {})


async def test_read_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.read_error = aiohttp.ClientError("stream interrupted")
    with pytest.raises(RetryableProviderError):
        await _adapter().complete("s", "u", {})


async def test_non_json_body_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = b"<html>not json</html>"
    with pytest.raises(TerminalProviderError):
        await _adapter().complete("s", "u", {})


async def test_blocked_prompt_is_terminal(fake_session: type[_FakeSession]) -> None:
    # A safety block returns no candidates; retrying the same prompt is futile.
    fake_session.body = {"promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(TerminalProviderError) as excinfo:
        await _adapter().complete("s", "u", {})
    assert "SAFETY" in str(excinfo.value)


async def test_no_candidates_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = {"candidates": []}
    with pytest.raises(TerminalProviderError):
        await _adapter().complete("s", "u", {})


async def test_empty_completion_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = {
        "candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]
    }
    with pytest.raises(TerminalProviderError) as excinfo:
        await _adapter().complete("s", "u", {})
    assert "MAX_TOKENS" in str(excinfo.value)


# --------------------------------------------------------------------------
# Missing credentials (no network at all)
# --------------------------------------------------------------------------


async def test_credential_failure_is_terminal_without_network(
    fake_session: type[_FakeSession],
) -> None:
    adapter = GeminiAdapter(
        token_source=_StubTokens(error=TerminalProviderError("no ADC"))  # type: ignore[arg-type]
    )
    with pytest.raises(TerminalProviderError):
        await adapter.complete("s", "u", {})
    assert fake_session.calls == []


async def test_missing_credentials_raises_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "google", None)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network should be attempted without credentials")

    monkeypatch.setattr(gemini.aiohttp, "ClientSession", _boom)

    with pytest.raises(TerminalProviderError):
        await GeminiAdapter().complete("s", "u", {})


# --------------------------------------------------------------------------
# Provider selection via get_llm
# --------------------------------------------------------------------------


def test_get_llm_selects_gemini_when_project_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    assert type(get_llm()).__name__ == "GeminiAdapter"


def test_get_llm_raises_when_project_unset() -> None:
    with pytest.raises(TerminalProviderError):
        get_llm()
