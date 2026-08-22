"""API tests: script upload, story graph retrieval, and manual line patches.

Fixture expectations (from tests/fixtures/sample.fountain + normalize semantics):
- 'FADE IN:' precedes the first slugline, so normalize emits a preamble scene
  with ordinal 0 plus 3 slugline scenes -> 4 scenes total.
- Cues MARA, TOM, TOM (CONT'D), MARA (V.O.) canonicalize to MARA and TOM.
- Dialogue attribution from cues: source 'cue', confidence 1.0.
The FDX fixture has no preamble content -> 3 scenes, same 2 characters.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_repo
from app.api.main import create_app
from app.api.repo import InMemoryRepository


@pytest.fixture
def client():
    app = create_app()
    repo = InMemoryRepository()
    app.dependency_overrides[get_repo] = lambda: repo
    with TestClient(app) as c:
        yield c


def auth_headers(client, email="writer@example.com", password="south-beacon-3"):
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


def upload(client, headers, project_id, filename, content: str):
    return client.post(
        f"/api/v1/projects/{project_id}/script",
        files={"file": (filename, content.encode("utf-8"), "application/octet-stream")},
        headers=headers,
    )


def test_fountain_upload_end_to_end(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)

    r = upload(client, headers, project_id, "sample.fountain", sample_fountain)
    assert r.status_code == 201
    body = r.json()
    assert body["script_id"]
    assert body["format"] == "fountain"
    assert body["scene_count"] == 4
    assert body["character_count"] == 2

    graph = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers).json()
    assert [s["ordinal"] for s in graph["scenes"]] == [0, 1, 2, 3]
    assert graph["scenes"][1]["slugline"] == "EXT. HARBOR TOWN - NIGHT"
    assert graph["scenes"][1]["interior"] is False
    assert graph["scenes"][1]["time_of_day"] == "NIGHT"
    assert {c["canonical_name"] for c in graph["characters"]} == {"MARA", "TOM"}

    dialogue = [
        line
        for scene in graph["scenes"]
        for line in scene["lines"]
        if line["kind"] == "dialogue"
    ]
    assert dialogue, "expected dialogue lines in the graph"
    for line in dialogue:
        assert line["attribution_source"] == "cue"
        assert line["attribution_confidence"] == 1.0
        assert line["character_name"] in {"MARA", "TOM"}


def test_txt_extension_parses_as_fountain(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    r = upload(client, headers, project_id, "sample.txt", sample_fountain)
    assert r.status_code == 201
    assert r.json()["format"] == "fountain"


def test_fdx_upload(client, sample_fdx):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    r = upload(client, headers, project_id, "sample.fdx", sample_fdx)
    assert r.status_code == 201
    body = r.json()
    assert body["format"] == "fdx"
    assert body["scene_count"] == 3
    assert body["character_count"] == 2


def test_unsupported_extension_415(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    r = upload(client, headers, project_id, "script.docx", "not a screenplay")
    assert r.status_code == 415


def test_graph_404_before_upload(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    r = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers)
    assert r.status_code == 404


def test_upload_to_other_users_project_404(client, sample_fountain):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = make_project(client, headers_a)
    r = upload(client, headers_b, project_id, "sample.fountain", sample_fountain)
    assert r.status_code == 404


def test_line_patch_sets_manual_attribution(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload(client, headers, project_id, "sample.fountain", sample_fountain)

    graph = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers).json()
    scene = next(s for s in graph["scenes"] if s["ordinal"] == 2)
    line = next(
        l for l in scene["lines"] if l["text"] == "You shouldn't be out in this."
    )
    assert line["character_name"] == "TOM"
    assert line["attribution_source"] == "cue"

    r = client.patch(
        f"/api/v1/projects/{project_id}/scenes/2/lines/{line['ordinal']}",
        json={"character_name": "TOM GRADY"},
        headers=headers,
    )
    assert r.status_code == 200
    patched = r.json()
    assert patched["character_name"] == "TOM GRADY"
    assert patched["attribution_source"] == "manual"
    assert patched["attribution_confidence"] == 1.0

    # The change persists in the stored graph.
    graph2 = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers).json()
    scene2 = next(s for s in graph2["scenes"] if s["ordinal"] == 2)
    line2 = next(l for l in scene2["lines"] if l["ordinal"] == line["ordinal"])
    assert line2["character_name"] == "TOM GRADY"
    assert line2["attribution_source"] == "manual"


def test_line_patch_text_only(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload(client, headers, project_id, "sample.fountain", sample_fountain)

    r = client.patch(
        f"/api/v1/projects/{project_id}/scenes/1/lines/1",
        json={"text": "Rain hammers the cobblestones harder than ever."},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["text"] == "Rain hammers the cobblestones harder than ever."


def test_line_patch_empty_body_422(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload(client, headers, project_id, "sample.fountain", sample_fountain)
    r = client.patch(
        f"/api/v1/projects/{project_id}/scenes/1/lines/1",
        json={},
        headers=headers,
    )
    assert r.status_code == 422


def test_line_patch_missing_scene_or_line_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload(client, headers, project_id, "sample.fountain", sample_fountain)

    r = client.patch(
        f"/api/v1/projects/{project_id}/scenes/99/lines/1",
        json={"character_name": "X"},
        headers=headers,
    )
    assert r.status_code == 404

    r = client.patch(
        f"/api/v1/projects/{project_id}/scenes/1/lines/999",
        json={"character_name": "X"},
        headers=headers,
    )
    assert r.status_code == 404
