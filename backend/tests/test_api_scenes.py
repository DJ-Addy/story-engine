"""API tests: shot list authoring, continuity findings, and deliberate marks.

The violating shot list puts consecutive sided shots on axis 'a' then 'b',
which trips the AXIS_CROSS rule under the default 'classical' profile.
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


def auth_headers(client, email="director@example.com", password="cliff-path-7"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def project_with_script(client, headers, sample_fountain, **project_kwargs):
    payload = {"title": "Wager", "rights_attested": True, **project_kwargs}
    project_id = client.post(
        "/api/v1/projects", json=payload, headers=headers
    ).json()["id"]
    r = client.post(
        f"/api/v1/projects/{project_id}/script",
        files={"file": ("sample.fountain", sample_fountain.encode("utf-8"))},
        headers=headers,
    )
    assert r.status_code == 201
    return project_id


def violating_shotlist(scene_ordinal=1):
    """Two consecutive sided close-ups that jump the axis (a -> b)."""
    return {
        "scene_ordinal": scene_ordinal,
        "action_axis": "MARA to lighthouse door",
        "shots": [
            {
                "ordinal": 1,
                "size": "cu",
                "subjects": ["MARA"],
                "axis_side": "a",
                "lens_mm": 50,
                "camera_height": "eye",
                "movement": "static",
                "eyeline": "left",
                "covers_lines": [1, 2, 3],
                "intent": "Close on Mara in the rain",
            },
            {
                "ordinal": 2,
                "size": "cu",
                "subjects": ["MARA"],
                "axis_side": "b",
                "lens_mm": 50,
                "camera_height": "eye",
                "movement": "static",
                "eyeline": "right",
                "covers_lines": [4],
                "intent": "Reverse from the wrong side",
            },
        ],
    }


def test_shotlist_post_returns_axis_cross_finding(client, sample_fountain):
    headers = auth_headers(client)
    project_id = project_with_script(client, headers, sample_fountain)

    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=violating_shotlist(),
        headers=headers,
    )
    assert r.status_code == 201
    findings = r.json()
    codes = [f["rule_code"] for f in findings]
    assert "AXIS_CROSS" in codes
    axis = next(f for f in findings if f["rule_code"] == "AXIS_CROSS")
    assert axis["id"]
    assert axis["severity"] == "warn"
    assert axis["shot_ordinal"] == 2
    assert axis["deliberate"] is False
    assert axis["deliberate_note"] is None


def test_shotlist_respects_grammar_profile(client, sample_fountain):
    """AXIS_CROSS is not registered for the 'handheld' profile."""
    headers = auth_headers(client)
    project_id = project_with_script(
        client, headers, sample_fountain, grammar_profile="handheld"
    )
    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=violating_shotlist(),
        headers=headers,
    )
    assert r.status_code == 201
    assert "AXIS_CROSS" not in [f["rule_code"] for f in r.json()]


def test_get_shots_roundtrip(client, sample_fountain):
    headers = auth_headers(client)
    project_id = project_with_script(client, headers, sample_fountain)
    client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=violating_shotlist(),
        headers=headers,
    )
    r = client.get(f"/api/v1/projects/{project_id}/scenes/1/shots", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["scene_ordinal"] == 1
    assert body["action_axis"] == "MARA to lighthouse door"
    assert [s["ordinal"] for s in body["shots"]] == [1, 2]
    assert body["shots"][0]["size"] == "cu"


def test_get_shots_404_before_post(client, sample_fountain):
    headers = auth_headers(client)
    project_id = project_with_script(client, headers, sample_fountain)
    r = client.get(f"/api/v1/projects/{project_id}/scenes/1/shots", headers=headers)
    assert r.status_code == 404


def test_shotlist_for_missing_scene_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = project_with_script(client, headers, sample_fountain)
    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/99/shotlist",
        json=violating_shotlist(scene_ordinal=99),
        headers=headers,
    )
    assert r.status_code == 404


def test_shotlist_invalid_body_422(client, sample_fountain):
    headers = auth_headers(client)
    project_id = project_with_script(client, headers, sample_fountain)
    bad = violating_shotlist()
    bad["shots"][1]["ordinal"] = 5  # non-contiguous ordinals fail schema validation
    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=bad,
        headers=headers,
    )
    assert r.status_code == 422


def test_patch_finding_marks_deliberate(client, sample_fountain):
    headers = auth_headers(client)
    project_id = project_with_script(client, headers, sample_fountain)
    findings = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=violating_shotlist(),
        headers=headers,
    ).json()
    axis = next(f for f in findings if f["rule_code"] == "AXIS_CROSS")

    r = client.patch(
        f"/api/v1/projects/{project_id}/scenes/1/findings/{axis['id']}",
        json={"deliberate": True, "deliberate_note": "Intentional disorientation beat"},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == axis["id"]
    assert body["deliberate"] is True
    assert body["deliberate_note"] == "Intentional disorientation beat"


def test_patch_unknown_finding_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = project_with_script(client, headers, sample_fountain)
    client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=violating_shotlist(),
        headers=headers,
    )
    r = client.patch(
        f"/api/v1/projects/{project_id}/scenes/1/findings/no-such-finding",
        json={"deliberate": True},
        headers=headers,
    )
    assert r.status_code == 404


def test_scene_routes_owner_isolation(client, sample_fountain):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = project_with_script(client, headers_a, sample_fountain)
    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=violating_shotlist(),
        headers=headers_b,
    )
    assert r.status_code == 404
