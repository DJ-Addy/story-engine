"""Unit tests for VeoAdapter. No network, no credentials, no real sleeping.

The HTTP transport is mocked by monkeypatching ``veo.aiohttp.ClientSession`` with
a fake session/response pair implementing the async-context-manager protocol (the
same approach as the retired ``test_runway``, which Veo's async job flow mirrors):
a POST to ``:predictLongRunning`` submits and a POST to ``:fetchPredictOperation``
polls, so the fake routes on the URL verb. The poll clock/sleep are injected into
the adapter so the loop runs instantly and its bound is exercised
deterministically, and the ADC bearer token comes from a stub ``TokenSource`` so
``google-auth`` is never touched.
"""

import base64
import json
import time

import aiohttp
import pytest

from app.adapters import veo
from app.adapters.base import (
    RetryableProviderError,
    TerminalProviderError,
    VideoProvider,
    VideoResult,
)
from app.adapters.veo import VeoAdapter, _snap_duration, _to_image_instance
from app.api.deps import get_video

TOKEN = "test-token"
PROJECT = "test-project"
LOCATION = "us-central1"
DEFAULT_MODEL = "veo-3.1-generate-001"
OPERATION = f"projects/{PROJECT}/locations/{LOCATION}/operations/op-1"

PNG_1PX = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16  # magic bytes + filler
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adapter defaults must not depend on the developer's environment."""
    for var in (
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_VEO_MODEL",
        "GOOGLE_VEO_STORAGE_URI",
    ):
        monkeypatch.delenv(var, raising=False)


def _done(
    videos: list[dict] | None = None, *, video_bytes: bytes | None = MP4, **response: object
) -> dict:
    """A finished operation payload carrying one inline clip by default."""
    if videos is None:
        videos = [{"bytesBase64Encoded": base64.b64encode(video_bytes or b"").decode("ascii")}]
    return {"name": OPERATION, "done": True, "response": {"videos": videos, **response}}


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
    """Stands in for aiohttp.ClientSession; routes submit vs poll, records calls."""

    submit_status: int = 200
    submit_body: object = {"name": OPERATION}
    poll_queue: list[tuple[int, object]] = []
    poll_default: tuple[int, object] = (200, {"name": OPERATION, "done": False})
    submit_error: Exception | None = None
    poll_error: Exception | None = None
    calls: list[dict] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        type(self).calls.append({"url": url, "json": json, "headers": headers})
        if url.endswith(":fetchPredictOperation"):
            if type(self).poll_error is not None:
                raise type(self).poll_error
            if type(self).poll_queue:
                status, body = type(self).poll_queue.pop(0)
            else:
                status, body = type(self).poll_default
            return _FakeResponse(status, body)
        if type(self).submit_error is not None:
            raise type(self).submit_error
        return _FakeResponse(type(self).submit_status, type(self).submit_body)

    @classmethod
    def submits(cls) -> list[dict]:
        return [call for call in cls.calls if call["url"].endswith(":predictLongRunning")]

    @classmethod
    def polls(cls) -> list[dict]:
        return [call for call in cls.calls if call["url"].endswith(":fetchPredictOperation")]


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch) -> type[_FakeSession]:
    _FakeSession.submit_status = 200
    _FakeSession.submit_body = {"name": OPERATION}
    _FakeSession.poll_queue = [(200, _done())]
    _FakeSession.poll_default = (200, {"name": OPERATION, "done": False})
    _FakeSession.submit_error = None
    _FakeSession.poll_error = None
    _FakeSession.calls = []
    monkeypatch.setattr(veo.aiohttp, "ClientSession", _FakeSession)
    return _FakeSession


class _Clock:
    """A fake monotonic clock advanced only by the fake sleep (never by reading)."""

    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def _adapter(**kwargs: object) -> tuple[VeoAdapter, _Clock, list[float]]:
    clock = _Clock()
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.t += seconds  # the injected sleep drives the injected clock forward

    kwargs.setdefault("token_source", _StubTokens())
    adapter = VeoAdapter(sleep=fake_sleep, clock=clock, **kwargs)  # type: ignore[arg-type]
    return adapter, clock, sleeps


# --------------------------------------------------------------------------
# Protocol conformance + construction
# --------------------------------------------------------------------------


def test_adapter_satisfies_protocol() -> None:
    adapter, _, _ = _adapter()
    assert isinstance(adapter, VideoProvider)
    assert adapter.name == "veo"


def test_construction_never_raises_without_credentials() -> None:
    # Must import/construct fine so offline collection works.
    assert VeoAdapter().name == "veo"
    assert VeoAdapter(project="p", location="europe-west4").name == "veo"


# --------------------------------------------------------------------------
# Pure duration snapping
# --------------------------------------------------------------------------


class TestSnapDuration:
    @pytest.mark.parametrize(
        ("requested", "expected"),
        [(0, 4), (3, 4), (4, 4), (5, 4), (6, 6), (7, 6), (8, 8), (9, 8), (30, 8)],
    )
    def test_snaps_to_a_legal_length(self, requested: int, expected: int) -> None:
        assert _snap_duration(requested) == expected

    def test_ties_round_down_so_a_clip_never_costs_more_than_asked(self) -> None:
        assert _snap_duration(5) == 4
        assert _snap_duration(7) == 6


# --------------------------------------------------------------------------
# Pure image coercion
# --------------------------------------------------------------------------


class TestToImageInstance:
    def test_gcs_uri_passes_through(self) -> None:
        assert _to_image_instance("gs://bucket/frame.png") == {
            "gcsUri": "gs://bucket/frame.png",
            "mimeType": "image/png",
        }

    def test_other_strings_are_assumed_base64(self) -> None:
        encoded = base64.b64encode(PNG_1PX).decode("ascii")
        assert _to_image_instance(encoded) == {
            "bytesBase64Encoded": encoded,
            "mimeType": "image/png",
        }

    def test_png_bytes_are_encoded(self) -> None:
        instance = _to_image_instance(PNG_1PX)
        assert instance["mimeType"] == "image/png"
        assert base64.b64decode(instance["bytesBase64Encoded"]) == PNG_1PX

    def test_jpeg_bytes_are_detected(self) -> None:
        assert _to_image_instance(JPEG)["mimeType"] == "image/jpeg"


# --------------------------------------------------------------------------
# Success path (mocked submit -> poll pending -> done)
# --------------------------------------------------------------------------


async def test_image_to_video_success(fake_session: type[_FakeSession]) -> None:
    fake_session.poll_queue = [
        (200, {"name": OPERATION, "done": False}),
        (200, _done()),
    ]
    adapter, _, sleeps = _adapter()
    result = await adapter.generate("a knight turns", image=PNG_1PX, duration_s=5)

    assert isinstance(result, VideoResult)
    assert result.provider == "veo"
    assert result.model == DEFAULT_MODEL
    assert result.video_bytes == MP4
    assert result.output_urls == []
    assert result.duration_ms == 4000  # 5s snapped down to a legal 4s
    assert result.cost_cents == 160  # 4s * 40 cents
    assert result.gen_params == {
        "operation_name": OPERATION,
        "duration_s": 4,
        "requested_duration_s": 5,
        "has_image": True,
        "prompt": "a knight turns",
        "aspect_ratio": "16:9",
        "location": LOCATION,
    }
    # Exactly one wait: between the pending poll and the done poll.
    assert sleeps == [10.0]

    submit = fake_session.submits()[-1]
    assert submit["url"] == (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}"
        f"/locations/{LOCATION}/publishers/google/models/{DEFAULT_MODEL}:predictLongRunning"
    )
    assert submit["headers"] == {
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json; charset=utf-8",
    }
    assert submit["json"] == {
        "instances": [
            {
                "prompt": "a knight turns",
                "image": {
                    "bytesBase64Encoded": base64.b64encode(PNG_1PX).decode("ascii"),
                    "mimeType": "image/png",
                },
            }
        ],
        "parameters": {"durationSeconds": 4, "aspectRatio": "16:9", "sampleCount": 1},
    }
    # No storageUri => Veo returns the clip inline, which is the default we want.
    assert "storageUri" not in submit["json"]["parameters"]


async def test_poll_targets_the_operation_endpoint(fake_session: type[_FakeSession]) -> None:
    adapter, _, _ = _adapter()
    await adapter.generate("x", image=PNG_1PX)

    poll = fake_session.polls()[-1]
    assert poll["url"] == (
        f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}"
        f"/locations/{LOCATION}/publishers/google/models/{DEFAULT_MODEL}:fetchPredictOperation"
    )
    assert poll["json"] == {"operationName": OPERATION}
    assert poll["headers"]["Authorization"] == f"Bearer {TOKEN}"


async def test_text_to_video_sends_prompt_only(fake_session: type[_FakeSession]) -> None:
    adapter, _, _ = _adapter()
    result = await adapter.generate("a wide desert at dawn", duration_s=8)

    assert result.gen_params["has_image"] is False
    assert result.duration_ms == 8000
    assert result.cost_cents == 320
    body = fake_session.submits()[-1]["json"]
    assert body["instances"] == [{"prompt": "a wide desert at dawn"}]
    assert body["parameters"]["durationSeconds"] == 8


async def test_image_only_generation_is_allowed(fake_session: type[_FakeSession]) -> None:
    adapter, _, _ = _adapter()
    result = await adapter.generate(None, image="gs://bucket/frame.png")

    assert result.gen_params["prompt"] is None
    assert result.gen_params["has_image"] is True
    assert fake_session.submits()[-1]["json"]["instances"] == [
        {"image": {"gcsUri": "gs://bucket/frame.png", "mimeType": "image/png"}}
    ]


async def test_neither_prompt_nor_image_is_terminal(fake_session: type[_FakeSession]) -> None:
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate(None)
    assert fake_session.calls == []  # never reached the network


# --------------------------------------------------------------------------
# Parameter plumbing
# --------------------------------------------------------------------------


async def test_params_map_onto_vertex_field_names(fake_session: type[_FakeSession]) -> None:
    adapter, _, _ = _adapter()
    await adapter.generate(
        "x",
        image=PNG_1PX,
        duration_s=6,
        params={
            "aspect_ratio": "9:16",
            "sample_count": 2,
            "negative_prompt": "blurry",
            "person_generation": "allow_adult",
            "resolution": "1080p",
            "seed": 42,
            "generate_audio": False,
            "enhancePrompt": True,  # caller-supplied extras pass through verbatim
        },
    )
    assert fake_session.submits()[-1]["json"]["parameters"] == {
        "durationSeconds": 6,
        "aspectRatio": "9:16",
        "sampleCount": 2,
        "negativePrompt": "blurry",
        "personGeneration": "allow_adult",
        "resolution": "1080p",
        "seed": 42,
        "generateAudio": False,
        "enhancePrompt": True,
    }


async def test_model_argument_overrides_the_default(fake_session: type[_FakeSession]) -> None:
    adapter, _, _ = _adapter()
    result = await adapter.generate(
        "x", image=PNG_1PX, duration_s=4, model="veo-3.1-fast-generate-001"
    )
    assert result.model == "veo-3.1-fast-generate-001"
    assert result.cost_cents == 40  # 4s * 10 cents for the fast model
    assert "veo-3.1-fast-generate-001:predictLongRunning" in fake_session.submits()[-1]["url"]


async def test_model_can_come_from_params_or_env(
    fake_session: type[_FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, _, _ = _adapter()
    result = await adapter.generate(
        "x", image=PNG_1PX, params={"model": "veo-3.1-fast-generate-001"}
    )
    assert result.model == "veo-3.1-fast-generate-001"
    # A params-supplied model is routing, not a generation parameter.
    assert "model" not in fake_session.submits()[-1]["json"]["parameters"]

    monkeypatch.setenv("GOOGLE_VEO_MODEL", "veo-2.0-generate-001")
    fake_session.poll_queue = [(200, _done())]
    adapter, _, _ = _adapter()
    result = await adapter.generate("x", image=PNG_1PX)
    assert result.model == "veo-2.0-generate-001"


async def test_storage_uri_switches_to_gcs_delivery(
    fake_session: type[_FakeSession],
) -> None:
    fake_session.poll_queue = [
        (200, _done(videos=[{"gcsUri": "gs://bucket/out/clip.mp4"}])),
    ]
    adapter, _, _ = _adapter()
    result = await adapter.generate(
        "x", image=PNG_1PX, params={"storage_uri": "gs://bucket/out/"}
    )

    assert fake_session.submits()[-1]["json"]["parameters"]["storageUri"] == "gs://bucket/out/"
    assert result.video_bytes == b""
    assert result.output_urls == ["gs://bucket/out/clip.mp4"]


async def test_storage_uri_can_come_from_env(
    fake_session: type[_FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_VEO_STORAGE_URI", "gs://env-bucket/out/")
    adapter, _, _ = _adapter()
    await adapter.generate("x", image=PNG_1PX)
    assert (
        fake_session.submits()[-1]["json"]["parameters"]["storageUri"]
        == "gs://env-bucket/out/"
    )


async def test_location_is_configurable(
    fake_session: type[_FakeSession], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "europe-west4")
    adapter, _, _ = _adapter()
    result = await adapter.generate("x", image=PNG_1PX)
    assert result.gen_params["location"] == "europe-west4"
    assert fake_session.submits()[-1]["url"].startswith(
        "https://europe-west4-aiplatform.googleapis.com/"
    )

    fake_session.poll_queue = [(200, _done())]
    adapter, _, _ = _adapter(location="asia-northeast1")  # explicit beats env
    await adapter.generate("x", image=PNG_1PX)
    assert fake_session.submits()[-1]["url"].startswith(
        "https://asia-northeast1-aiplatform.googleapis.com/"
    )


# --------------------------------------------------------------------------
# Operation failure + poll bound
# --------------------------------------------------------------------------


async def test_failed_operation_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.poll_queue = [
        (200, {"name": OPERATION, "done": False}),
        (200, {"name": OPERATION, "done": True, "error": {"message": "content policy"}}),
    ]
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError) as excinfo:
        await adapter.generate("x", image=PNG_1PX)
    assert "content policy" in str(excinfo.value)


async def test_safety_filtered_result_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.poll_queue = [
        (
            200,
            {
                "name": OPERATION,
                "done": True,
                "response": {
                    "videos": [],
                    "raiMediaFilteredCount": 1,
                    "raiMediaFilteredReasons": ["58061214"],
                },
            },
        )
    ]
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError) as excinfo:
        await adapter.generate("x", image=PNG_1PX)
    assert "safety" in str(excinfo.value)


async def test_done_with_no_videos_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.poll_queue = [(200, {"name": OPERATION, "done": True, "response": {}})]
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_non_base64_video_bytes_are_terminal(
    fake_session: type[_FakeSession],
) -> None:
    fake_session.poll_queue = [(200, _done(videos=[{"bytesBase64Encoded": "A"}]))]
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_submit_without_operation_name_is_terminal(
    fake_session: type[_FakeSession],
) -> None:
    fake_session.submit_body = {"metadata": {}}
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)
    assert fake_session.polls() == []  # never started polling


async def test_poll_loop_respects_bound_with_injected_clock(
    fake_session: type[_FakeSession],
) -> None:
    # The operation never finishes: every poll returns done=False (empty queue).
    fake_session.poll_queue = []
    adapter, clock, sleeps = _adapter(poll_interval_s=5.0, poll_timeout_s=20.0)

    start = time.monotonic()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)
    elapsed = time.monotonic() - start

    # deadline 0+20: polls at t=0,5,10,15,20 (5 POSTs) with 4 waits in between.
    assert len(fake_session.polls()) == 5
    assert sleeps == [5.0, 5.0, 5.0, 5.0]
    assert clock.t == pytest.approx(20.0)
    assert elapsed < 1.0  # injected sleep => no real waiting


# --------------------------------------------------------------------------
# HTTP status classification (submit + poll)
# --------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_submit_retryable_statuses(
    fake_session: type[_FakeSession], status: int
) -> None:
    fake_session.submit_status = status
    fake_session.submit_body = b"Server is sad"
    adapter, _, _ = _adapter()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_submit_terminal_statuses(
    fake_session: type[_FakeSession], status: int
) -> None:
    fake_session.submit_status = status
    fake_session.submit_body = b"Bad Request: bad duration"
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_poll_500_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.poll_queue = [(500, b"Internal Server Error")]
    adapter, _, _ = _adapter()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_poll_403_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.poll_queue = [(403, b"Forbidden")]
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_submit_network_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.submit_error = aiohttp.ClientError("connection reset")
    adapter, _, _ = _adapter()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_poll_network_error_is_retryable(fake_session: type[_FakeSession]) -> None:
    fake_session.poll_error = aiohttp.ClientError("stream interrupted")
    adapter, _, _ = _adapter()
    with pytest.raises(RetryableProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_non_json_submit_body_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.submit_body = b"<html>not json</html>"
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


async def test_non_json_poll_body_is_terminal(fake_session: type[_FakeSession]) -> None:
    fake_session.poll_queue = [(200, b"<html>not json</html>")]
    adapter, _, _ = _adapter()
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)


# --------------------------------------------------------------------------
# Missing credentials (no network at all)
# --------------------------------------------------------------------------


async def test_credential_failure_is_terminal_without_network(
    fake_session: type[_FakeSession],
) -> None:
    adapter, _, _ = _adapter(token_source=_StubTokens(error=TerminalProviderError("no ADC")))
    with pytest.raises(TerminalProviderError):
        await adapter.generate("x", image=PNG_1PX)
    assert fake_session.calls == []


async def test_missing_credentials_raises_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("no network should be attempted without credentials")

    monkeypatch.setattr(veo.aiohttp, "ClientSession", _boom)

    # No GOOGLE_CLOUD_PROJECT => the URL cannot even be built.
    with pytest.raises(TerminalProviderError):
        await VeoAdapter().generate("x", image=PNG_1PX)


# --------------------------------------------------------------------------
# Cost estimate
# --------------------------------------------------------------------------


class TestEstimateCost:
    def test_uses_the_snapped_duration(self) -> None:
        adapter = VeoAdapter()
        assert adapter.estimate_cost_cents(5) == 160  # snaps to 4s * 40 cents
        assert adapter.estimate_cost_cents(6) == 240
        assert adapter.estimate_cost_cents(8) == 320

    def test_per_model_rates(self) -> None:
        adapter = VeoAdapter()
        assert adapter.estimate_cost_cents(8, model="veo-3.1-fast-generate-001") == 80
        assert adapter.estimate_cost_cents(8, model="veo-2.0-generate-001") == 400

    def test_unknown_model_uses_the_conservative_fallback(self) -> None:
        assert VeoAdapter().estimate_cost_cents(4, model="veo-9.9-imaginary") == 160

    def test_env_model_applies_when_unspecified(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_VEO_MODEL", "veo-3.1-fast-generate-001")
        assert VeoAdapter().estimate_cost_cents(4) == 40

    def test_never_free(self) -> None:
        assert VeoAdapter().estimate_cost_cents(0) >= 1


# --------------------------------------------------------------------------
# Provider selection via get_video
# --------------------------------------------------------------------------


def test_get_video_selects_veo_when_project_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    assert type(get_video()).__name__ == "VeoAdapter"


def test_get_video_raises_when_project_unset() -> None:
    with pytest.raises(TerminalProviderError):
        get_video()
