"""API tests: storyboard frame render, serve and scene-wide status.

get_image is overridden with a deterministic FakeImage so nothing hits the
network or spends credits; a thin recording subclass remembers the prompt and
seed the endpoint built so the served bytes can be checked against what the
fake would produce for exactly that call. Gating (rights -> 403, cost cap ->
402) mirrors the video endpoint's conventions, and the last test closes the
loop: a board on file is what flips render_video to image-to-video.
"""

from collections.abc import Iterable

import pytest
from fastapi.testclient import TestClient

from app.adapters.base import TerminalProviderError
from app.adapters.fake import FakeImage, FakeVideo, _deterministic_bytes
from app.analytics.events import AnalyticsEvent, CostEvent, RenderEvent
from app.api.deps import get_image, get_repo, get_video
from app.api.main import create_app
from app.api.repo import InMemoryRepository
from app.api.routers.analytics import get_analytics_recorder

BOARD_SUFFIX = (
    ", storyboard frame, cinematic composition, 16:9, no text, no captions, "
    "no watermark"
)


class _RecordingImage(FakeImage):
    """FakeImage that remembers what it was asked."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.calls: list[dict] = []

    async def generate(self, prompt: str, seed: int, params: dict):
        self.calls.append({"prompt": prompt, "seed": seed, "params": dict(params)})
        return await super().generate(prompt, seed, params)


class _FailsOnSecondCall(_RecordingImage):
    """Succeeds, then fails once, then succeeds again — a mid-batch outage."""

    async def generate(self, prompt: str, seed: int, params: dict):
        if len(self.calls) == 1:
            self.calls.append({"prompt": prompt, "seed": seed, "params": dict(params)})
            raise TerminalProviderError("injected outage on the second board")
        return await super().generate(prompt, seed, params)


class _CapturingRecorder:
    """Stands in for EventRecorder; keeps every event the routes emit."""

    def __init__(self) -> None:
        self.events: list[AnalyticsEvent] = []

    def record(self, events: Iterable[AnalyticsEvent]) -> int:
        batch = list(events)
        self.events.extend(batch)
        return len(batch)


@pytest.fixture(autouse=True)
def _instant_retries(monkeypatch):
    """The board route backs off for real seconds on a 429; not in tests."""
    from app.api.routers import boards

    monkeypatch.setattr(boards, "_IMAGE_BASE_DELAY_S", 0.0)


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def fake_image():
    return _RecordingImage()


@pytest.fixture
def fake_video():
    return FakeVideo()


@pytest.fixture
def recorder():
    return _CapturingRecorder()


def _build_client(repo, image, video=None, recorder=None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_image] = lambda: image
    if video is not None:
        app.dependency_overrides[get_video] = lambda: video
    if recorder is not None:
        app.dependency_overrides[get_analytics_recorder] = lambda: recorder
    return TestClient(app)


@pytest.fixture
def client(repo, fake_image, fake_video, recorder):
    with _build_client(repo, fake_image, fake_video, recorder) as c:
        yield c


def auth_headers(client, email="boards@example.com", password="salt-marsh-4"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_project(client, headers, title="Wager"):
    r = client.post(
        "/api/v1/projects",
        json={"title": title, "rights_attested": True},
        headers=headers,
    )
    return r.json()["id"]


def upload_script(client, headers, project_id, sample_fountain):
    r = client.post(
        f"/api/v1/projects/{project_id}/script",
        files={"file": ("sample.fountain", sample_fountain.encode("utf-8"))},
        headers=headers,
    )
    assert r.status_code == 201


def _shot(ordinal: int) -> dict:
    return {
        "ordinal": ordinal,
        "size": "ws" if ordinal == 1 else "cu",
        "subjects": ["MARA"],
        "axis_side": "a",
        "lens_mm": 35,
        "camera_height": "eye",
        "movement": "static",
        "eyeline": "none",
        "covers_lines": [ordinal],
        "intent": f"Beat {ordinal} on Mara in the rain",
    }


def post_shotlist(client, headers, project_id, scene_ordinal=1, n_shots=1):
    body = {
        "scene_ordinal": scene_ordinal,
        "action_axis": "MARA to lighthouse door",
        "shots": [_shot(i) for i in range(1, n_shots + 1)],
    }
    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/{scene_ordinal}/shotlist",
        json=body,
        headers=headers,
    )
    assert r.status_code == 201


def setup_project(client, headers, sample_fountain, n_shots=1):
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    post_shotlist(client, headers, project_id, n_shots=n_shots)
    return project_id


def _board_url(project_id, scene_ordinal=1, shot_ordinal=1) -> str:
    return (
        f"/api/v1/projects/{project_id}/scenes/{scene_ordinal}"
        f"/shots/{shot_ordinal}/board"
    )


def render_board(client, headers, project_id, scene_ordinal=1, shot_ordinal=1):
    return client.post(_board_url(project_id, scene_ordinal, shot_ordinal), headers=headers)


def get_board(client, headers, project_id, scene_ordinal=1, shot_ordinal=1):
    return client.get(_board_url(project_id, scene_ordinal, shot_ordinal), headers=headers)


def render_scene_boards(client, headers, project_id, scene_ordinal=1, force=False):
    return client.post(
        f"/api/v1/projects/{project_id}/scenes/{scene_ordinal}/boards",
        json={"force": force},
        headers=headers,
    )


def scene_boards_status(client, headers, project_id, scene_ordinal=1):
    return client.get(
        f"/api/v1/projects/{project_id}/scenes/{scene_ordinal}/boards", headers=headers
    )


def render_video(client, headers, project_id, scene_ordinal=1, shot_ordinal=1):
    return client.post(
        f"/api/v1/projects/{project_id}/render/video",
        json={"scene_ordinal": scene_ordinal, "shot_ordinal": shot_ordinal, "duration_s": 5},
        headers=headers,
    )


def _expected_bytes(call: dict) -> bytes:
    return _deterministic_bytes("image", call["prompt"], str(call["seed"]))


# --------------------------------------------------------------------------
# Single shot: render + serve
# --------------------------------------------------------------------------


def test_render_board_then_fetch_frame(client, fake_image, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)

    r = render_board(client, headers, project_id)
    assert r.status_code == 201
    assert r.json() == {
        "scene_ordinal": 1,
        "shot_ordinal": 1,
        "cost_cents": 4,
        "provider": "fake-image",
        "model": "fake-image-v1",
        "source": "text",
    }

    call = fake_image.calls[-1]
    assert "MARA" in call["prompt"]
    assert call["prompt"].endswith(BOARD_SUFFIX)

    frame = get_board(client, headers, project_id)
    assert frame.status_code == 200
    assert frame.headers["content-type"] == "image/png"
    assert frame.content == _expected_bytes(call)


def test_board_seed_is_stable_per_shot(client, fake_image, sample_fountain):
    """A re-render of the same shot is a retry of the same roll; another shot
    is a different roll."""
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain, n_shots=2)

    assert render_board(client, headers, project_id, shot_ordinal=1).status_code == 201
    assert render_board(client, headers, project_id, shot_ordinal=1).status_code == 201
    assert render_board(client, headers, project_id, shot_ordinal=2).status_code == 201

    seeds = [c["seed"] for c in fake_image.calls]
    assert seeds[0] == seeds[1]
    assert seeds[0] != seeds[2]
    assert all(0 <= s < 2**31 for s in seeds)


def test_get_board_404_before_render(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    r = get_board(client, headers, project_id)
    assert r.status_code == 404
    assert r.json()["detail"] == "No board rendered yet for scene 1 shot 1"


def test_render_board_records_spend(client, repo, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    render_board(client, headers, project_id)
    assert repo.get_project(project_id).cost_spent_cents == 4
    render_board(client, headers, project_id)
    assert repo.get_project(project_id).cost_spent_cents == 8


# --------------------------------------------------------------------------
# Gating
# --------------------------------------------------------------------------


def test_render_board_requires_rights_attestation(client, repo, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    repo.get_project(project_id).rights_attested = False
    r = render_board(client, headers, project_id)
    assert r.status_code == 403
    assert r.json()["detail"] == (
        "Rights not attested for this project; cannot render boards"
    )


def test_render_board_cost_cap_exceeded_402(client, repo, fake_image, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    repo.get_project(project_id).cost_cap_cents = 1
    r = render_board(client, headers, project_id)
    assert r.status_code == 402
    assert r.json()["detail"] == (
        "Cost cap exceeded: spent 0c + estimated 4c would exceed cap 1c"
    )
    # Refused before the provider was touched, and nothing was booked or stored.
    assert fake_image.calls == []
    assert repo.get_project(project_id).cost_spent_cents == 0
    assert repo.get_shot_frame(project_id, 1, 1) is None


def test_render_board_cap_boundary_passes_then_blocks(client, repo, sample_fountain):
    """spent + estimated == cap passes; the next identical render exceeds it."""
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    repo.get_project(project_id).cost_cap_cents = 8  # exactly two boards
    assert render_board(client, headers, project_id).status_code == 201
    assert render_board(client, headers, project_id).status_code == 201
    assert render_board(client, headers, project_id).status_code == 402


def test_render_board_missing_script_404(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    assert render_board(client, headers, project_id).status_code == 404


def test_render_board_missing_scene_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    assert render_board(client, headers, project_id, scene_ordinal=99).status_code == 404


def test_render_board_missing_shotlist_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    r = render_board(client, headers, project_id)
    assert r.status_code == 404
    assert r.json()["detail"] == "No shot list for scene 1"


def test_render_board_missing_shot_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    assert render_board(client, headers, project_id, shot_ordinal=99).status_code == 404


def test_render_board_owner_isolation(client, sample_fountain):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = setup_project(client, headers_a, sample_fountain)
    assert render_board(client, headers_b, project_id).status_code == 404
    assert get_board(client, headers_b, project_id).status_code == 404


# --------------------------------------------------------------------------
# Provider failures
# --------------------------------------------------------------------------


def test_render_board_terminal_error_503_and_nothing_saved(repo, sample_fountain):
    with _build_client(repo, FakeImage(terminal_fail=True)) as client:
        headers = auth_headers(client)
        project_id = setup_project(client, headers, sample_fountain)
        r = render_board(client, headers, project_id)
        assert r.status_code == 503
        assert get_board(client, headers, project_id).status_code == 404
    assert repo.get_shot_frame(project_id, 1, 1) is None
    assert repo.get_project(project_id).cost_spent_cents == 0


def test_render_board_retryable_error_502(repo, sample_fountain):
    with _build_client(repo, FakeImage(fail_times=1)) as client:
        headers = auth_headers(client)
        project_id = setup_project(client, headers, sample_fountain)
        # A transient failure is retried inside the request (a per-minute
        # quota is the expected shape of it), so the first call succeeds.
        assert render_board(client, headers, project_id).status_code == 201


# --------------------------------------------------------------------------
# Scene-wide render
# --------------------------------------------------------------------------


def test_scene_boards_renders_all_then_skips_then_force(
    client, repo, fake_image, sample_fountain
):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain, n_shots=2)

    first = render_scene_boards(client, headers, project_id)
    assert first.status_code == 201
    body = first.json()
    assert body["scene_ordinal"] == 1
    assert [b["shot_ordinal"] for b in body["rendered"]] == [1, 2]
    assert body["skipped"] == []
    assert body["total_cost_cents"] == 8
    assert all(b["source"] == "text" for b in body["rendered"])
    assert len(fake_image.calls) == 2

    # Every shot now has a frame; a second call spends nothing.
    second = render_scene_boards(client, headers, project_id)
    assert second.status_code == 201
    assert second.json() == {
        "scene_ordinal": 1,
        "rendered": [],
        "skipped": [1, 2],
        "total_cost_cents": 0,
        "failed": [],
    }
    assert len(fake_image.calls) == 2

    forced = render_scene_boards(client, headers, project_id, force=True)
    assert forced.status_code == 201
    assert [b["shot_ordinal"] for b in forced.json()["rendered"]] == [1, 2]
    assert forced.json()["skipped"] == []
    assert len(fake_image.calls) == 4
    assert repo.get_project(project_id).cost_spent_cents == 16


def test_scene_boards_only_renders_shots_without_a_frame(client, fake_image, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain, n_shots=2)
    assert render_board(client, headers, project_id, shot_ordinal=2).status_code == 201

    r = render_scene_boards(client, headers, project_id)
    assert r.status_code == 201
    assert [b["shot_ordinal"] for b in r.json()["rendered"]] == [1]
    assert r.json()["skipped"] == [2]
    assert len(fake_image.calls) == 2


def test_scene_boards_refuses_whole_batch_before_spending(
    client, repo, fake_image, sample_fountain
):
    """One board fits under the cap, two do not: nothing is rendered."""
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain, n_shots=2)
    repo.get_project(project_id).cost_cap_cents = 4

    r = render_scene_boards(client, headers, project_id)
    assert r.status_code == 402
    assert r.json()["detail"] == (
        "Cost cap exceeded: spent 0c + estimated 8c would exceed cap 4c"
    )
    assert fake_image.calls == []
    assert repo.get_shot_frame(project_id, 1, 1) is None
    assert repo.get_shot_frame(project_id, 1, 2) is None
    assert repo.get_project(project_id).cost_spent_cents == 0


def test_scene_boards_requires_rights_attestation(client, repo, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    repo.get_project(project_id).rights_attested = False
    assert render_scene_boards(client, headers, project_id).status_code == 403


def test_scene_boards_404_without_shotlist(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    r = render_scene_boards(client, headers, project_id)
    assert r.status_code == 404
    assert r.json()["detail"] == "No shot list for scene 1"


def test_scene_boards_404_without_script(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    assert render_scene_boards(client, headers, project_id).status_code == 404


def test_scene_boards_keeps_frames_rendered_before_a_failure(repo, sample_fountain):
    image = _FailsOnSecondCall()
    with _build_client(repo, image) as client:
        headers = auth_headers(client)
        project_id = setup_project(client, headers, sample_fountain, n_shots=2)

        r = render_scene_boards(client, headers, project_id)
        # The batch does not stop at the failure: what was drawn is kept and
        # booked, and the failed shot is reported with the provider's reason,
        # so an animatic missing one frame is delivered rather than aborted.
        assert r.status_code == 201
        body = r.json()
        assert [b["shot_ordinal"] for b in body["rendered"]] == [1]
        assert [f["shot_ordinal"] for f in body["failed"]] == [2]
        assert body["failed"][0]["detail"]
        assert repo.get_shot_frame(project_id, 1, 1) is not None
        assert repo.get_shot_frame(project_id, 1, 2) is None
        assert repo.get_project(project_id).cost_spent_cents == 4

        # Re-running finishes the job without re-buying shot 1.
        retry = render_scene_boards(client, headers, project_id)
        assert retry.status_code == 201
        assert [b["shot_ordinal"] for b in retry.json()["rendered"]] == [2]
        assert retry.json()["failed"] == []
        assert retry.json()["skipped"] == [1]
        assert repo.get_project(project_id).cost_spent_cents == 8


# --------------------------------------------------------------------------
# Scene-wide status (read-only)
# --------------------------------------------------------------------------


def test_scene_boards_status_is_empty_before_anything_exists(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    # No shot list yet: the normal opening state, not an error.
    r = scene_boards_status(client, headers, project_id)
    assert r.status_code == 200
    assert r.json() == {"scene_ordinal": 1, "rendered": [], "total": 0}

    post_shotlist(client, headers, project_id, n_shots=2)
    r = scene_boards_status(client, headers, project_id)
    assert r.status_code == 200
    assert r.json() == {"scene_ordinal": 1, "rendered": [], "total": 2}


def test_scene_boards_status_lists_rendered_ordinals(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain, n_shots=2)

    assert render_board(client, headers, project_id, shot_ordinal=2).status_code == 201
    assert scene_boards_status(client, headers, project_id).json() == {
        "scene_ordinal": 1,
        "rendered": [2],
        "total": 2,
    }

    assert render_scene_boards(client, headers, project_id).status_code == 201
    assert scene_boards_status(client, headers, project_id).json() == {
        "scene_ordinal": 1,
        "rendered": [1, 2],
        "total": 2,
    }

    # A forced re-render replaces frames; it does not change what exists.
    assert render_scene_boards(client, headers, project_id, force=True).status_code == 201
    assert scene_boards_status(client, headers, project_id).json()["rendered"] == [1, 2]


def test_scene_boards_status_404_without_script(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    r = scene_boards_status(client, headers, project_id)
    assert r.status_code == 404
    assert r.json()["detail"] == "No script uploaded yet"


def test_scene_boards_status_owner_isolation(client, sample_fountain):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = setup_project(client, headers_a, sample_fountain)
    assert scene_boards_status(client, headers_b, project_id).status_code == 404


# --------------------------------------------------------------------------
# Analytics: the governor's decision and the render share one run_id
# --------------------------------------------------------------------------


def test_render_board_emits_cost_and_render_events(client, recorder, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    recorder.events.clear()

    assert render_board(client, headers, project_id).status_code == 201

    costs = [e for e in recorder.events if isinstance(e, CostEvent)]
    renders = [e for e in recorder.events if isinstance(e, RenderEvent)]
    assert len(costs) == 1 and len(renders) == 1
    cost, render = costs[0], renders[0]
    assert cost.operation == "render_board"
    assert cost.allowed is True
    assert cost.estimated_cents == 4
    assert cost.provider == "fake-image"
    assert (cost.scene_ordinal, cost.shot_ordinal) == (1, 1)
    assert render.kind == "board"
    assert render.source == "text"
    assert render.status == "ok"
    assert render.cost_cents == 4
    assert render.estimated_cost_cents == 4
    assert render.clip_count == 1
    assert render.run_id == cost.run_id


def test_refused_board_emits_only_a_cost_event(client, repo, recorder, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    repo.get_project(project_id).cost_cap_cents = 1
    recorder.events.clear()

    assert render_board(client, headers, project_id).status_code == 402

    assert [type(e) for e in recorder.events] == [CostEvent]
    refusal = recorder.events[0]
    assert refusal.operation == "render_board"
    assert refusal.allowed is False
    assert refusal.headroom_cents == 1 - 4


def test_refused_scene_batch_is_one_event_at_scene_grain(
    client, repo, recorder, sample_fountain
):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain, n_shots=2)
    repo.get_project(project_id).cost_cap_cents = 4
    recorder.events.clear()

    assert render_scene_boards(client, headers, project_id).status_code == 402

    assert [type(e) for e in recorder.events] == [CostEvent]
    refusal = recorder.events[0]
    assert refusal.allowed is False
    assert refusal.estimated_cents == 8
    assert (refusal.scene_ordinal, refusal.shot_ordinal) == (1, 0)


# --------------------------------------------------------------------------
# The point of the whole thing: a board turns the video render image-to-video
# --------------------------------------------------------------------------


def test_render_video_after_board_uses_image_path(client, fake_video, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)

    assert render_video(client, headers, project_id).json()["source"] == "text"
    assert fake_video.calls[-1]["has_image"] is False

    assert render_board(client, headers, project_id).status_code == 201

    r = render_video(client, headers, project_id)
    assert r.status_code == 201
    assert r.json()["source"] == "image"
    assert fake_video.calls[-1]["has_image"] is True



class _BlocksFirstPrompt(_RecordingImage):
    """A content block on the first attempt, success on the retry.

    Mirrors the live failure: the shot designer wrote "men bind Ulysses to
    the mast", and the image model answered `no candidates (blocked: SAFETY)`.
    """

    def __init__(self) -> None:
        super().__init__()
        self._blocked_once = False

    async def generate(self, prompt: str, seed: int, params: dict):
        if not self._blocked_once:
            self._blocked_once = True
            self.calls.append({"prompt": prompt, "seed": seed, "params": dict(params)})
            raise TerminalProviderError("gemini-image returned no candidates (blocked: SAFETY)")
        return await super().generate(prompt, seed, params)


def test_a_safety_block_is_retried_once_without_the_intent(repo, sample_fountain):
    image = _BlocksFirstPrompt()
    with _build_client(repo, image) as client:
        headers = auth_headers(client)
        project_id = setup_project(client, headers, sample_fountain, n_shots=1)

        r = render_board(client, headers, project_id)
        assert r.status_code == 201, r.text
        assert len(image.calls) == 2
        first, second = image.calls[0]["prompt"], image.calls[1]["prompt"]
        # The retry is a different, shorter prompt that keeps the frame's
        # composition (the style suffix survives) and drops the free text.
        assert second != first
        assert len(second) < len(first)
        assert "storyboard" in second.lower()
        assert repo.get_shot_frame(project_id, 1, 1) is not None
        # Paid once: the blocked attempt cost nothing in the ledger.
        assert repo.get_project(project_id).cost_spent_cents == 4


def test_a_non_safety_terminal_error_is_not_retried(repo, sample_fountain):
    with _build_client(repo, FakeImage(terminal_fail=True)) as client:
        headers = auth_headers(client)
        project_id = setup_project(client, headers, sample_fountain, n_shots=1)
        assert render_board(client, headers, project_id).status_code == 503
        assert repo.get_shot_frame(project_id, 1, 1) is None
