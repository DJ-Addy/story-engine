"""API tests: project creation, rights attestation, and owner isolation."""

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


def auth_headers(client, email="owner@example.com", password="lantern-swing-9"):
    client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    r = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_create_project_defaults(client):
    headers = auth_headers(client)
    r = client.post(
        "/api/v1/projects",
        json={"title": "The Lighthouse Wager", "rights_attested": True},
        headers=headers,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["id"]
    assert body["title"] == "The Lighthouse Wager"
    assert body["grammar_profile"] == "classical"
    assert body["validator_mode"] == "strict"
    assert body["rights_attested"] is True
    assert body["cost_cap_cents"] == 15000
    assert body["cost_spent_cents"] == 0


def test_rights_attested_false_rejected_422(client):
    headers = auth_headers(client)
    r = client.post(
        "/api/v1/projects",
        json={"title": "Unattested", "rights_attested": False},
        headers=headers,
    )
    assert r.status_code == 422


def test_invalid_grammar_profile_422(client):
    headers = auth_headers(client)
    r = client.post(
        "/api/v1/projects",
        json={
            "title": "Bad profile",
            "rights_attested": True,
            "grammar_profile": "dogme95",
        },
        headers=headers,
    )
    assert r.status_code == 422


def test_list_returns_own_projects_only(client):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    client.post(
        "/api/v1/projects",
        json={"title": "A's project", "rights_attested": True},
        headers=headers_a,
    )
    client.post(
        "/api/v1/projects",
        json={"title": "B's project", "rights_attested": True},
        headers=headers_b,
    )

    titles_a = [p["title"] for p in client.get("/api/v1/projects", headers=headers_a).json()]
    titles_b = [p["title"] for p in client.get("/api/v1/projects", headers=headers_b).json()]
    assert titles_a == ["A's project"]
    assert titles_b == ["B's project"]


def test_get_project_by_id(client):
    headers = auth_headers(client)
    created = client.post(
        "/api/v1/projects",
        json={"title": "Fetch me", "rights_attested": True},
        headers=headers,
    ).json()
    r = client.get(f"/api/v1/projects/{created['id']}", headers=headers)
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_other_users_project_is_404(client):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    created = client.post(
        "/api/v1/projects",
        json={"title": "A's secret", "rights_attested": True},
        headers=headers_a,
    ).json()
    r = client.get(f"/api/v1/projects/{created['id']}", headers=headers_b)
    assert r.status_code == 404


def test_missing_project_404(client):
    headers = auth_headers(client)
    r = client.get("/api/v1/projects/no-such-id", headers=headers)
    assert r.status_code == 404
