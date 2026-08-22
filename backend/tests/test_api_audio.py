"""API tests: scene audio render + serve ("crisp listen" path).

get_tts is overridden with FakeTTS in every test here so nothing touches the
network or spends real credits; the default EdgeTTSAdapter is only ever
constructed lazily inside app.api.deps.get_tts, never at import time.
"""

import pytest
from fastapi.testclient import TestClient

from app.adapters.fake import FakeTTS
from app.api.deps import get_repo, get_tts
from app.api.main import create_app
from app.api.repo import InMemoryRepository


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def client(repo):
    app = create_app()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_tts] = lambda: FakeTTS()
    with TestClient(app) as c:
        yield c


def auth_headers(client, email="engineer@example.com", password="salt-marsh-4"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_project(client, headers, rights_attested=True, title="Wager"):
    r = client.post(
        "/api/v1/projects",
        json={"title": title, "rights_attested": rights_attested},
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
    return r


def test_render_audio_then_fetch_wav(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
    )
    assert r.status_code == 201
    body = r.json()
    assert body["scene_ordinal"] == 1
    assert body["duration_ms"] > 0
    assert body["clip_count"] > 0
    assert isinstance(body["ambience_tags"], list)

    audio = client.get(f"/api/v1/projects/{project_id}/scenes/1/audio", headers=headers)
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/wav"
    assert audio.content
    assert audio.content.startswith(b"RIFF")


def test_audio_404_before_render(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    r = client.get(f"/api/v1/projects/{project_id}/scenes/1/audio", headers=headers)
    assert r.status_code == 404


def test_render_audio_requires_rights_attestation(client, repo, sample_fountain):
    # POST /projects itself rejects rights_attested=False (422) before a
    # project can even exist, so exercise the render-time gate by flipping
    # the flag directly on the stored (unfrozen) ProjectRecord afterwards.
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    repo.get_project(project_id).rights_attested = False

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
    )
    assert r.status_code == 403


def test_render_audio_missing_scene_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/99/render/audio", headers=headers
    )
    assert r.status_code == 404


def test_render_audio_owner_isolation(client, sample_fountain):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = make_project(client, headers_a)
    upload_script(client, headers_a, project_id, sample_fountain)

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers_b
    )
    assert r.status_code == 404


def test_render_audio_cost_cap_exceeded_402(client, repo, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    # ProjectRecord is a plain (unfrozen) pydantic model held by reference in
    # the in-memory repo, so mutating it here directly shrinks the cap the
    # running app sees -- no API surface exists yet to set cost_cap_cents at
    # project-creation time.
    project = repo.get_project(project_id)
    project.cost_cap_cents = 1

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
    )
    assert r.status_code == 402
