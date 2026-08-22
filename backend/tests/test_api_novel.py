"""API tests: novel manuscript ingest (prose -> Fountain -> story graph).

Fixture text: '"Hello," said Mara.\\n\\n"Hello yourself," Tom replied.' is a
single scene body (no chapter heading / scene break) with two adjacent-tag
quotes, both attributable at confidence 0.9 -> needs_review == 0. It
round-trips through parse_fountain + normalize as one slugline-less scene
(ordinal 1, since novel_to_fountain always emits a forced '.SCENE N'
slugline) with two characters, MARA and TOM.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_repo
from app.api.main import create_app
from app.api.repo import InMemoryRepository

NOVEL_TEXT = '"Hello," said Mara.\n\n"Hello yourself," Tom replied.'


@pytest.fixture
def client():
    app = create_app()
    repo = InMemoryRepository()
    app.dependency_overrides[get_repo] = lambda: repo
    with TestClient(app) as c:
        yield c


def auth_headers(client, email="novelist@example.com", password="quiet-lantern-9"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_project(client, headers, title="Manuscript"):
    r = client.post(
        "/api/v1/projects",
        json={"title": title, "rights_attested": True},
        headers=headers,
    )
    return r.json()["id"]


def post_novel(client, headers, project_id, filename="novel.txt", content=NOVEL_TEXT):
    return client.post(
        f"/api/v1/projects/{project_id}/novel",
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
        headers=headers,
    )


def preview_novel(client, headers, project_id, filename="novel.txt", content=NOVEL_TEXT):
    return client.post(
        f"/api/v1/projects/{project_id}/novel/preview",
        files={"file": (filename, content.encode("utf-8"), "text/plain")},
        headers=headers,
    )


def test_novel_upload_persists_as_screenplay(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)

    r = post_novel(client, headers, project_id)
    assert r.status_code == 201
    body = r.json()
    assert body["script_id"]
    assert body["scene_count"] > 0
    assert body["character_count"] > 0
    assert body["quotes"] == 2
    assert body["attributed"] == 2
    assert body["needs_review"] == 0
    assert set(body["characters"]) == {"Mara", "Tom"}

    graph = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers).json()
    assert len(graph["scenes"]) == body["scene_count"]
    assert {c["canonical_name"] for c in graph["characters"]} == {"MARA", "TOM"}
    dialogue = [
        line
        for scene in graph["scenes"]
        for line in scene["lines"]
        if line["kind"] == "dialogue"
    ]
    assert {line["character_name"] for line in dialogue} == {"MARA", "TOM"}
    for line in dialogue:
        assert line["attribution_source"] == "cue"
        assert line["attribution_confidence"] == 1.0


def test_novel_preview_does_not_persist(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)

    r = preview_novel(client, headers, project_id)
    assert r.status_code == 200
    body = r.json()
    assert body["quotes"] == 2
    assert body["attributed"] == 2
    assert body["needs_review"] == 0
    assert set(body["characters"]) == {"Mara", "Tom"}
    assert "fountain_text" in body
    assert "MARA" in body["fountain_text"]
    assert "TOM" in body["fountain_text"]

    # Nothing was saved: the graph endpoint still 404s.
    graph_resp = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers)
    assert graph_resp.status_code == 404


def test_novel_preview_after_upload_does_not_overwrite_graph(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    post_novel(client, headers, project_id)
    graph_before = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers).json()

    r = preview_novel(client, headers, project_id, content='"Different," said Nova.')
    assert r.status_code == 200

    graph_after = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers).json()
    assert graph_after == graph_before


def test_novel_upload_rejects_non_txt_415(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    r = post_novel(client, headers, project_id, filename="manuscript.pdf", content="not text")
    assert r.status_code == 415


def test_novel_preview_rejects_non_txt_415(client):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    r = preview_novel(client, headers, project_id, filename="manuscript.pdf", content="not text")
    assert r.status_code == 415


def test_novel_upload_to_other_users_project_404(client):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = make_project(client, headers_a)
    r = post_novel(client, headers_b, project_id)
    assert r.status_code == 404
