"""API tests: Runway-backed shot video render + serve.

get_video is overridden with a deterministic FakeVideo so nothing hits the
network or spends credits. The fake records each generate() call, letting us
assert the image-vs-text path the endpoint selected. Gating (rights -> 403,
cost cap -> 402) mirrors the scene audio endpoint's conventions.
"""

import pytest
from fastapi.testclient import TestClient

from app.adapters.base import VideoResult
from app.adapters.fake import FakeVideo
from app.api.deps import get_repo, get_video
from app.api.main import create_app
from app.api.repo import InMemoryRepository

PNG_1PX = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def fake_video():
    return FakeVideo()


@pytest.fixture
def client(repo, fake_video):
    app = create_app()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_video] = lambda: fake_video
    with TestClient(app) as c:
        yield c


def auth_headers(client, email="video@example.com", password="salt-marsh-4"):
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


def post_shotlist(client, headers, project_id, scene_ordinal=1):
    body = {
        "scene_ordinal": scene_ordinal,
        "action_axis": "MARA to lighthouse door",
        "shots": [
            {
                "ordinal": 1,
                "size": "ws",
                "subjects": ["MARA"],
                "axis_side": "a",
                "lens_mm": 35,
                "camera_height": "eye",
                "movement": "static",
                "eyeline": "none",
                "covers_lines": [1, 3],
                "intent": "Establish Mara in the rain",
            }
        ],
    }
    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/{scene_ordinal}/shotlist",
        json=body,
        headers=headers,
    )
    assert r.status_code == 201


def setup_project(client, headers, sample_fountain):
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    post_shotlist(client, headers, project_id)
    return project_id


def render_video(client, headers, project_id, scene_ordinal=1, shot_ordinal=1, duration_s=5):
    return client.post(
        f"/api/v1/projects/{project_id}/render/video",
        json={
            "scene_ordinal": scene_ordinal,
            "shot_ordinal": shot_ordinal,
            "duration_s": duration_s,
        },
        headers=headers,
    )


# --------------------------------------------------------------------------
# Success (text-to-video, the default when no per-shot frame exists)
# --------------------------------------------------------------------------


def test_render_video_text_path_then_fetch_clip(client, fake_video, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)

    r = render_video(client, headers, project_id, duration_s=5)
    assert r.status_code == 201
    body = r.json()
    assert body["scene_ordinal"] == 1
    assert body["shot_ordinal"] == 1
    assert body["source"] == "text"
    assert body["duration_ms"] == 5000
    assert body["cost_cents"] == 25  # 5s * 5 cents
    assert body["provider"] == "fake-video"
    assert body["has_video"] is True
    assert body["output_urls"]

    # No per-shot frame stored => text-to-video was used.
    assert fake_video.calls[-1]["has_image"] is False
    assert fake_video.calls[-1]["duration_s"] == 5

    clip = client.get(
        f"/api/v1/projects/{project_id}/render/video/1/1", headers=headers
    )
    assert clip.status_code == 200
    assert clip.headers["content-type"] == "video/mp4"
    assert clip.content


def test_render_video_image_path_when_frame_present(
    client, repo, fake_video, sample_fountain
):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    # A previz board/frame exists for this shot => prefer image-to-video.
    repo.save_shot_frame(project_id, 1, 1, PNG_1PX)

    r = render_video(client, headers, project_id)
    assert r.status_code == 201
    assert r.json()["source"] == "image"
    assert fake_video.calls[-1]["has_image"] is True


def test_render_video_records_spend(client, repo, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    render_video(client, headers, project_id, duration_s=10)
    assert repo.get_project(project_id).cost_spent_cents == 50  # 10s * 5 cents


# --------------------------------------------------------------------------
# Gating
# --------------------------------------------------------------------------


def test_render_video_requires_rights_attestation(client, repo, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    repo.get_project(project_id).rights_attested = False
    r = render_video(client, headers, project_id)
    assert r.status_code == 403


def test_render_video_cost_cap_exceeded_402(client, repo, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    repo.get_project(project_id).cost_cap_cents = 1
    r = render_video(client, headers, project_id, duration_s=5)
    assert r.status_code == 402


def test_render_video_missing_scene_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    r = render_video(client, headers, project_id, scene_ordinal=99)
    assert r.status_code == 404


def test_render_video_missing_shotlist_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    # Scene exists but no shot list posted yet.
    r = render_video(client, headers, project_id, scene_ordinal=1, shot_ordinal=1)
    assert r.status_code == 404


def test_render_video_missing_shot_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    r = render_video(client, headers, project_id, shot_ordinal=99)
    assert r.status_code == 404


def test_render_video_owner_isolation(client, sample_fountain):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = setup_project(client, headers_a, sample_fountain)
    r = render_video(client, headers_b, project_id)
    assert r.status_code == 404


def test_render_video_cap_boundary_passes_then_blocks(client, repo, sample_fountain):
    """spent + estimated == cap passes; the next identical render exceeds it."""
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    repo.get_project(project_id).cost_cap_cents = 50  # exactly two 5s renders
    assert render_video(client, headers, project_id, duration_s=5).status_code == 201
    assert render_video(client, headers, project_id, duration_s=5).status_code == 201
    assert render_video(client, headers, project_id, duration_s=5).status_code == 402


# --------------------------------------------------------------------------
# Serve
# --------------------------------------------------------------------------


def test_get_video_404_before_render(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_project(client, headers, sample_fountain)
    r = client.get(f"/api/v1/projects/{project_id}/render/video/1/1", headers=headers)
    assert r.status_code == 404


def test_get_video_returns_urls_when_not_downloaded(repo, sample_fountain):
    """When the provider returns URLs only (no bytes), the GET hands back JSON."""

    class _UrlOnlyVideo:
        name = "url-only"

        async def generate(self, prompt=None, *, image=None, duration_s=5,
                           model=None, params=None):
            return VideoResult(
                video_bytes=b"",
                output_urls=["https://cdn/clip.mp4"],
                duration_ms=duration_s * 1000,
                cost_cents=self.estimate_cost_cents(duration_s),
                provider=self.name,
                model="url-only-v1",
                gen_params={},
            )

        def estimate_cost_cents(self, duration_s, model=None):
            return max(1, duration_s * 5)

    app = create_app()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_video] = lambda: _UrlOnlyVideo()
    with TestClient(app) as client:
        headers = auth_headers(client)
        project_id = setup_project(client, headers, sample_fountain)
        assert render_video(client, headers, project_id).status_code == 201
        clip = client.get(
            f"/api/v1/projects/{project_id}/render/video/1/1", headers=headers
        )
        assert clip.status_code == 200
        assert clip.headers["content-type"].startswith("application/json")
        assert clip.json()["output_urls"] == ["https://cdn/clip.mp4"]
