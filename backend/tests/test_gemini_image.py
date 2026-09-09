"""Unit tests for GeminiImageAdapter. No network, no credentials, no credits.

Same harness as ``test_gemini``: ``aiohttp.ClientSession`` is monkeypatched
with a fake session/response pair implementing the async-context-manager
protocol, and the ADC bearer token comes from a stub ``TokenSource`` so
``google-auth`` is never touched. This exercises the real ``generate`` path —
URL construction, the image-specific request body, status classification,
``inlineData`` decoding and the flat per-image pricing — offline.
"""

import base64
import json

import aiohttp
import pytest

from app.adapters import gemini_image
from app.adapters.base import (
    ImageProvider,
    ImageResult,
    RetryableProviderError,
    TerminalProviderError,
)
from app.adapters.gemini_image import GeminiImageAdapter
from app.api.deps import get_image

TOKEN = "test-token"
PROJECT = "test-project"
DEFAULT_MODEL = "gemini-2.5-flash-image"
PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(32))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter defaults must not depend on the developer's environment."""
    for var in (
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_GEMINI_IMAGE_MODEL",
        "GOOGLE_GEMINI_LOCATION",
    ):
        monkeypatch.delenv(var, raising=False)


def _image_response(
    data: bytes = PNG, mime: str = "image/png", leading_text: str | None = None
) -> dict:
    parts: list[dict] = []
    if leading_text is not None:
        parts.append({"text": leading_text})
    parts.append(
        {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode("ascii")}}
    )
    return {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]
    }


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
    _FakeSession.body = _image_response()
    _FakeSession.post_error = None
    _FakeSession.read_error = None
    _FakeSession.calls = []
    monkeypatch.setattr(gemini_image.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


def _adapter(**kwargs: object) -> GeminiImageAdapter:
    return GeminiImageAdapter(token_source=_StubTokens(), **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Protocol conformance + construction
# --------------------------------------------------------------------------


def test_adapter_satisfies_protocol() -> None:
    assert isinstance(_adapter(), ImageProvider)
    assert _adapter().name == "gemini-image"


def test_construction_never_raises_without_credentials() -> None:
    assert GeminiImageAdapter().name == "gemini-image"
    assert GeminiImageAdapter(project="p", location="us-central1", model="m").model == "m"


class TestModelSelection:
    def test_default_model(self) -> None:
        assert _adapter().model == DEFAULT_MODEL

    def test_env_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_GEMINI_IMAGE_MODEL", "gemini-3-pro-image")
        assert GeminiImageAdapter().model == "gemini-3-pro-image"

    def test_constructor_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_GEMINI_IMAGE_MODEL", "gemini-3-pro-image")
        assert GeminiImageAdapter(model=DEFAULT_MODEL).model == DEFAULT_MODEL

    def test_text_model_env_does_not_leak_in(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The text adapter's knob must not silently repoint the image adapter
        # at a model that cannot return images.
        monkeypatch.setenv("GOOGLE_GEMINI_MODEL", "gemini-3.8-flash")
        assert GeminiImageAdapter().model == DEFAULT_MODEL


# --------------------------------------------------------------------------
# Endpoint construction
# --------------------------------------------------------------------------


class TestUrl:
    def test_global_endpoint_has_no_region_prefix(self) -> None:
        assert _adapter()._url(DEFAULT_MODEL) == (
            "https://aiplatform.googleapis.com/v1/projects/test-project"
            "/locations/global/publishers/google/models/"
            "gemini-2.5-flash-image:generateContent"
        )

    def test_regional_endpoint_is_host_prefixed(self) -> None:
        assert _adapter(location="us-central1")._url(DEFAULT_MODEL) == (
            "https://us-central1-aiplatform.googleapis.com/v1/projects/test-project"
            "/locations/us-central1/publishers/google/models/"
            "gemini-2.5-flash-image:generateContent"
        )

    def test_env_selects_location(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_GEMINI_LOCATION", "europe-west4")
        adapter = GeminiImageAdapter(token_source=_StubTokens())  # type: ignore[arg-type]
        assert "europe-west4-aiplatform.googleapis.com" in adapter._url(DEFAULT_MODEL)


# --------------------------------------------------------------------------
# Success path (mocked HTTP)
# --------------------------------------------------------------------------


async def test_generate_returns_decoded_image(fake_session: type[_FakeSession]) -> None:
    result = await _adapter().generate("a lighthouse in the rain", 12345, {})

    assert isinstance(result, ImageResult)
    assert result.image_bytes == PNG
    assert result.provider == "gemini-image"
    assert result.model == DEFAULT_MODEL
    assert result.seed == 12345
    assert result.cost_cents == 4
    assert result.gen_params["mime_type"] == "image/png"


async def test_request_shape_and_auth_header(fake_session: type[_FakeSession]) -> None:
    await _adapter().generate("PROMPT", 7, {})
    call = fake_session.calls[-1]

    assert call["url"] == (
        "https://aiplatform.googleapis.com/v1/projects/test-project"
        "/locations/global/publishers/google/models/"
        "gemini-2.5-flash-image:generateContent"
    )
    assert call["headers"] == {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    assert call["json"] == {
        "contents": [{"role": "user", "parts": [{"text": "PROMPT"}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "16:9"},
        },
    }


async def test_seed_is_not_sent_on_the_wire(fake_session: type[_FakeSession]) -> None:
    # The verified request shape has no seed; see GeminiImageAdapter.generate.
    await _adapter().generate("p", 99, {})
    assert "seed" not in json.dumps(fake_session.calls[-1]["json"])


async def test_params_merge_into_generation_config_and_win(
    fake_session: type[_FakeSession],
) -> None:
    await _adapter().generate("p", 1, {"imageConfig": {"aspectRatio": "1:1"}, "candidateCount": 1})
    assert fake_session.calls[-1]["json"]["generationConfig"] == {
        "responseModalities": ["IMAGE"],
        "imageConfig": {"aspectRatio": "1:1"},
        "candidateCount": 1,
    }


async def test_model_param_overrides_and_is_not_leaked_into_config(
    fake_session: type[_FakeSession],
) -> None:
    result = await _adapter().generate("p", 1, {"model": "gemini-3-pro-image"})
    call = fake_session.calls[-1]

    assert result.model == "gemini-3-pro-image"
    assert "gemini-3-pro-image:generateContent" in call["url"]
    assert "model" not in call["json"]["generationConfig"]


async def test_caller_params_are_not_mutated(fake_session: type[_FakeSession]) -> None:
    params = {"model": "gemini-3-pro-image", "candidateCount": 1}
    await _adapter().generate("p", 1, params)
    assert params == {"model": "gemini-3-pro-image", "candidateCount": 1}


async def test_first_inline_data_part_wins_over_leading_text(
    fake_session: type[_FakeSession],
) -> None:
    fake_session.body = _image_response(leading_text="Here is your frame:")
    result = await _adapter().generate("p", 1, {})
    assert result.image_bytes == PNG


async def test_reported_mime_type_is_kept(fake_session: type[_FakeSession]) -> None:
    fake_session.body = _image_response(mime="image/jpeg")
    result = await _adapter().generate("p", 1, {})
    assert result.gen_params["mime_type"] == "image/jpeg"


# --------------------------------------------------------------------------
# Cost accounting
# --------------------------------------------------------------------------


def test_estimate_is_flat_per_image() -> None:
    adapter = GeminiImageAdapter()
    assert adapter.estimate_cost_cents(1) == 4
    assert adapter.estimate_cost_cents(3) == 12
    assert adapter.estimate_cost_cents(0) == 0


# --------------------------------------------------------------------------
# Not supported today
# --------------------------------------------------------------------------


async def test_generate_from_refs_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError) as excinfo:
        await _adapter().generate_from_refs("p", [PNG], 1, {})
    assert "generate()" in str(excinfo.value)


# --------------------------------------------------------------------------
# Error paths (mocked HTTP)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_retryable_http_statuses(fake_session: type[_FakeSession], status: int) -> None:
    fake_session.status = status
    fake_session.body = b"Server is sad"
    with pytest.raises(RetryableProviderError):
        await _adapter().generate("p", 1, {})


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_terminal_http_statuses(fake_session: type[_FakeSession], status: int) -> None:
    fake_session.status = status
    fake_session.body = b"Bad Request: unknown model"
    with pytest.raises(TerminalProviderError):
        await _adapter().generate("p", 1, {})


async def test_network_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.post_error = aiohttp.ClientError("connection reset")
    with pytest.raises(RetryableProviderError):
        await _adapter().generate("p", 1, {})


async def test_read_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.read_error = aiohttp.ClientError("stream interrupted")
    with pytest.raises(RetryableProviderError):
        await _adapter().generate("p", 1, {})


async def test_non_json_body_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = b"<html>not json</html>"
    with pytest.raises(TerminalProviderError):
        await _adapter().generate("p", 1, {})


async def test_blocked_prompt_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = {"promptFeedback": {"blockReason": "SAFETY"}}
    with pytest.raises(TerminalProviderError) as excinfo:
        await _adapter().generate("p", 1, {})
    assert "SAFETY" in str(excinfo.value)


async def test_no_candidates_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = {"candidates": []}
    with pytest.raises(TerminalProviderError):
        await _adapter().generate("p", 1, {})


async def test_text_only_answer_is_terminal_and_names_finish_reason(
    fake_session: type[_FakeSession],
) -> None:
    # The model answered with words instead of a picture (a refusal, usually).
    fake_session.body = {
        "candidates": [
            {"content": {"parts": [{"text": "I can't draw that."}]}, "finishReason": "IMAGE_SAFETY"}
        ]
    }
    with pytest.raises(TerminalProviderError) as excinfo:
        await _adapter().generate("p", 1, {})
    assert "IMAGE_SAFETY" in str(excinfo.value)


async def test_empty_parts_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}
    with pytest.raises(TerminalProviderError) as excinfo:
        await _adapter().generate("p", 1, {})
    assert "MAX_TOKENS" in str(excinfo.value)


async def test_undecodable_image_data_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.body = {
        "candidates": [
            {"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": "@@not-b64@@"}}]}}
        ]
    }
    with pytest.raises(TerminalProviderError) as excinfo:
        await _adapter().generate("p", 1, {})
    assert "undecodable" in str(excinfo.value)


# --------------------------------------------------------------------------
# Missing credentials (no network at all)
# --------------------------------------------------------------------------


async def test_credential_failure_is_terminal_without_network(
    fake_session: type[_FakeSession],
) -> None:
    adapter = GeminiImageAdapter(
        token_source=_StubTokens(error=TerminalProviderError("no ADC"))  # type: ignore[arg-type]
    )
    with pytest.raises(TerminalProviderError):
        await adapter.generate("p", 1, {})
    assert fake_session.calls == []


async def test_missing_credentials_raises_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "google", None)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network should be attempted without credentials")

    monkeypatch.setattr(gemini_image.aiohttp, "ClientSession", _boom)

    with pytest.raises(TerminalProviderError):
        await GeminiImageAdapter().generate("p", 1, {})


# --------------------------------------------------------------------------
# Provider selection via get_image
# --------------------------------------------------------------------------


def test_get_image_selects_gemini_image_when_project_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    assert type(get_image()).__name__ == "GeminiImageAdapter"


def test_get_image_raises_when_project_unset() -> None:
    with pytest.raises(TerminalProviderError) as excinfo:
        get_image()
    assert "no image provider configured" in str(excinfo.value)
