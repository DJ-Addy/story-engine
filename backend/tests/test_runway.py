"""Unit tests for RunwayAdapter. No network, no real key, no real sleeping.

The HTTP transport is mocked by monkeypatching ``runway.aiohttp.ClientSession``
with a fake session/response pair implementing the async-context-manager
protocol (the same approach as ``test_elevenlabs``), extended for Runway's async
job flow: ``post`` submits, ``get`` on ``/tasks/{id}`` polls, and any other
``get`` downloads the output clip. The poll clock/sleep are injected into the
adapter so the poll loop runs instantly and its bound is exercised deterministically.
"""

import json
import time

import aiohttp
import pytest

from app.adapters import runway
from app.adapters.base import (
    RetryableProviderError,
    TerminalProviderError,
    VideoProvider,
    VideoResult,
)
from app.adapters.runway import RunwayAdapter, _to_prompt_image
from app.api.deps import get_video

KEY = "test-key"
PNG_1PX = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # magic bytes + filler


# --------------------------------------------------------------------------
# Fake aiohttp transport
# --------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, body: object) -> None:
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")

    async def __aenter__(self) -> "_FakeResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def read(self) -> bytes:
        return self._body


class _FakeSession:
    """Stands in for aiohttp.ClientSession; routes post/get and records calls."""

    submit_status: int = 200
    submit_body: object = {"id": "task-1"}
    task_queue: list[tuple[int, object]] = []
    task_default: tuple[int, object] = (200, {"status": "RUNNING"})
    download_status: int = 200
    download_body: bytes = b"VIDEO-BYTES"
    post_error: Exception | None = None
    get_error: Exception | None = None
    post_calls: list[dict] = []
    get_calls: list[dict] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        type(self).post_calls.append({"url": url, "json": json, "headers": headers})
        if type(self).post_error is not None:
            raise type(self).post_error
        return _FakeResponse(type(self).submit_status, type(self).submit_body)

    def get(self, url: str, headers: dict | None = None):
        type(self).get_calls.append({"url": url, "headers": headers})
        if type(self).get_error is not None:
            raise type(self).get_error
        if "/tasks/" in url:
            if type(self).task_queue:
                status, body = type(self).task_queue.pop(0)
            else:
                status, body = type(self).task_default
            return _FakeResponse(status, body)
        return _FakeResponse(type(self).download_status, type(self).download_body)


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSession]:
    _FakeSession.submit_status = 200
    _FakeSession.submit_body = {"id": "task-1"}
    _FakeSession.task_queue = []
    _FakeSession.task_default = (200, {"status": "RUNNING"})
    _FakeSession.download_status = 200
    _FakeSession.download_body = b"VIDEO-BYTES"
    _FakeSession.post_error = None
    _FakeSession.get_error = None
    _FakeSession.post_calls = []
    _FakeSession.get_calls = []
    monkeypatch.setattr(runway.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


class _Clock:
    """A fake monotonic clock advanced only by the fake sleep (never by reading)."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _adapter(**kwargs: object) -> tuple[RunwayAdapter, _Clock, list[float]]:
    clock = _Clock()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.t += seconds  # the injected sleep drives the injected clock forward

    adapter = RunwayAdapter(api_key=KEY, sleep=fake_sleep, clock=clock, **kwargs)
    return adapter, clock, sleeps


# --------------------------------------------------------------------------
# Protocol conformance + construction
# --------------------------------------------------------------------------


def test_adapter_satisfies_protocol() -> None:
    adapter, _, _ = _adapter()
    assert isinstance(adapter, VideoProvider)
    assert adapter.name == "runway"


def test_construction_never_raises_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNWAY_API_KEY", raising=False)
    # Must import/construct fine so offline collection works.
    assert RunwayAdapter().name == "runway"


# --------------------------------------------------------------------------
# Pure prompt-image coercion
# --------------------------------------------------------------------------


class TestToPromptImage:
    def test_url_string_passes_through(self) -> None:
        assert _to_prompt_image("https://cdn/frame.png") == "https://cdn/frame.png"

    def test_png_bytes_become_data_uri(self) -> None:
        uri = _to_prompt_image(PNG_1PX)
        assert uri.startswith("data:image/png;base64,")

    def test_jpeg_bytes_detected(self) -> None:
        uri = _to_prompt_image(b"\xff\xd8\xff\xe0" + b"\x00" * 8)
        assert uri.startswith("data:image/jpeg;base64,")


# --------------------------------------------------------------------------
# Success path (mocked submit -> poll RUNNING -> SUCCEEDED -> download)
# --------------------------------------------------------------------------


async def test_image_to_video_success(fake_session: type[_FakeSession]) -> None:
    fake_session.task_queue = [
        (200, {"status": "RUNNING"}),
        (200, {"status": "SUCCEEDED", "output": ["https://cdn/clip.mp4"]}),
    ]
    adapter, _, sleeps = _adapter()
    result = await adapter.generate("a knight turns", image=PNG_1PX, duration_s=5)

    assert isinstance(result, VideoResult)
    assert result.provider == "runway"
    assert result.model == "gen4_turbo"
    assert result.output_urls == ["https://cdn/clip.mp4"]
    assert result.video_bytes == b"VIDEO-BYTES"
    assert result.duration_ms == 5000
    assert result.cost_cents == 25  # 5s * 5 cents
    assert result.gen_params["has_image"] is True
    assert result.gen_params["task_id"] == "task-1"
    # Exactly one wait: between the RUNNING poll and the SUCCEEDED poll.
    assert sleeps == [5.0]

    submit = fake_session.post_calls[-1]
    assert submit["url"] == "https://api.dev.runwayml.com/v1/image_to_video"
    assert submit["headers"]["Authorization"] == f"Bearer {KEY}"
    assert submit["headers"]["X-Runway-Version"] == "2024-11-06"
    assert submit["json"]["promptImage"].startswith("data:image/png;base64,")
    assert submit["json"]["model"] == "gen4_turbo"
    assert submit["json"]["duration"] == 5


async def test_text_to_video_uses_text_endpoint(fake_session: type[_FakeSession]) -> None:
    fake_session.task_queue = [
        (200, {"status": "SUCCEEDED", "output": ["https://cdn/clip.mp4"]}),
    ]
    adapter, _, _ = _adapter()
    result = await adapter.generate("a wide desert at dawn", duration_s=10)

    assert result.gen_params["has_image"] is False
    assert result.duration_ms == 10000
    submit = fake_session.post_calls[-1]
    assert submit["url"] == "https://api.dev.runwayml.com/v1/text_to_video"
    assert submit["json"]["promptText"] == "a wide desert at dawn"
    assert "promptImage" not in submit["json"]


async def test_params_and_model_override(fake_session: type[_FakeSession]) -> None:
    fake_session.task_queue = [
        (200, {"status": "SUCCEEDED", "output": ["https://cdn/clip.mp4"]}),
    ]
    adapter, _, _ = _adapter()
    await adapter.generate(
        "x", image=PNG_1PX, model="gen3a_turbo", params={"ratio": "1584:672", "seed": 42}
    )
    body = fake_session.post_calls[-1]["json"]
    assert body["model"] == "gen3a_turbo"
    assert body["ratio"] == "1584:672"
    assert body["seed"] == 42


async def test_download_false_returns_urls_only(fake_session: type[_FakeSession]) -> None:
    fake_session.task_queue = [
        (200, {"status": "SUCCEEDED", "output": ["https://cdn/clip.mp4"]}),
    ]
    adapter, _, _ = _adapter()
    result = await adapter.generate("x", image=PNG_1PX, download=False)
    assert result.video_bytes == b""
    assert result.output_urls == ["https://cdn/clip.mp4"]
    # No non-task GET (i.e. no download) should have been issued.
    assert all("/tasks/" in call["url"] for call in fake_session.get_calls)


async def test_no_image_and_no_prompt_is_terminal(fake_session: type[_FakeSession]) -> None:
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate(None)


async def test_succeeded_without_output_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.task_queue = [(200, {"status": "SUCCEEDED", "output": []})]
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


# --------------------------------------------------------------------------
# Task failure + poll bound
# --------------------------------------------------------------------------


async def test_failed_task_raises_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.task_queue = [
        (200, {"status": "RUNNING"}),
        (200, {"status": "FAILED", "failure": "content policy violation"}),
    ]
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_poll_loop_respects_bound_with_injected_clock(
    fake_session: type[_FakeSession],
) -> None:
    # Task never finishes: every /tasks/ GET returns RUNNING (empty queue -> default).
    fake_session.task_queue = []
    fake_session.task_default = (200, {"status": "RUNNING"})
    adapter, clock, sleeps = _adapter(poll_interval_s=5.0, poll_timeout_s=20.0)

    start = time.monotonic()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)
    elapsed = time.monotonic() - start

    # deadline 0+20: polls at t=0,5,10,15,20 (5 GETs) with 4 waits in between.
    assert len(fake_session.get_calls) == 5
    assert sleeps == [5.0, 5.0, 5.0, 5.0]
    assert clock.t == pytest.approx(20.0)
    assert elapsed < 1.0  # injected sleep => no real waiting


# --------------------------------------------------------------------------
# HTTP status classification (submit + poll)
# --------------------------------------------------------------------------


async def test_submit_429_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.submit_status = 429
    fake_session.submit_body = b"Too Many Requests"
    adapter, _, _ = _adapter()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_submit_500_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.submit_status = 500
    fake_session.submit_body = b"Internal Server Error"
    adapter, _, _ = _adapter()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_submit_400_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.submit_status = 400
    fake_session.submit_body = b"Bad Request"
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_poll_500_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.task_queue = [(500, b"Internal Server Error")]
    adapter, _, _ = _adapter()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_poll_403_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.task_queue = [(403, b"Forbidden")]
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_submit_network_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.post_error = aiohttp.ClientError("connection reset")
    adapter, _, _ = _adapter()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_poll_network_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.get_error = aiohttp.ClientError("stream interrupted")
    adapter, _, _ = _adapter()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)


# --------------------------------------------------------------------------
# Missing key (no network at all)
# --------------------------------------------------------------------------


async def test_missing_key_raises_without_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNWAY_API_KEY", raising=False)

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network should be attempted without a key")

    monkeypatch.setattr(runway.aiohttp, "ClientSession", _boom)

    adapter = RunwayAdapter(api_key=None)
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


# --------------------------------------------------------------------------
# Cost estimate
# --------------------------------------------------------------------------


def test_estimate_cost_scales_with_duration() -> None:
    adapter = RunwayAdapter(api_key=KEY)
    assert adapter.estimate_cost_cents(0) == 1  # floor of 1
    assert adapter.estimate_cost_cents(5) == 25  # 5s * 5 cents
    assert adapter.estimate_cost_cents(10) == 50
    assert adapter.estimate_cost_cents(5, model="gen3a_turbo") == 25  # model accepted


# --------------------------------------------------------------------------
# Provider selection via get_video
# --------------------------------------------------------------------------


def test_get_video_selects_runway_when_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNWAY_API_KEY", "rw-key")
    assert type(get_video()).__name__ == "RunwayAdapter"


def test_get_video_raises_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNWAY_API_KEY", raising=False)
    with pytest.raises(TerminalProviderError):
        get_video()
