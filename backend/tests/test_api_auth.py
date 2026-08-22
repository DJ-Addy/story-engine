"""API tests: registration, login, and token-protected access."""

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


CREDS = {"email": "mara@example.com", "password": "beacon-oil-1"}


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_register_returns_user(client):
    r = client.post("/api/v1/auth/register", json=CREDS)
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == CREDS["email"]
    assert body["id"]
    assert "password" not in body
    assert "password_hash" not in body


def test_register_duplicate_email_409(client):
    assert client.post("/api/v1/auth/register", json=CREDS).status_code == 201
    r = client.post("/api/v1/auth/register", json=CREDS)
    assert r.status_code == 409


def test_login_returns_token_pair(client):
    client.post("/api/v1/auth/register", json=CREDS)
    r = client.post("/api/v1/auth/login", json=CREDS)
    assert r.status_code == 200
    body = r.json()
    assert body["access_token"]
    assert body["token_type"] == "bearer"


def test_login_wrong_password_401(client):
    client.post("/api/v1/auth/register", json=CREDS)
    r = client.post(
        "/api/v1/auth/login",
        json={"email": CREDS["email"], "password": "wrong-password"},
    )
    assert r.status_code == 401


def test_login_unknown_email_401(client):
    r = client.post("/api/v1/auth/login", json=CREDS)
    assert r.status_code == 401


def test_protected_route_without_token_401(client):
    r = client.get("/api/v1/projects")
    assert r.status_code == 401


def test_protected_route_with_garbage_token_401(client):
    r = client.get(
        "/api/v1/projects", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert r.status_code == 401


def test_token_grants_access(client):
    client.post("/api/v1/auth/register", json=CREDS)
    token = client.post("/api/v1/auth/login", json=CREDS).json()["access_token"]
    r = client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json() == []
