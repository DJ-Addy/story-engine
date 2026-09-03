"""API tests: scene timeline endpoint (time-aligned lanes for the scrubbable UI).

get_tts is overridden with FakeTTS so nothing hits the network. Because FakeTTS
clip durations are deterministic, the exact lane timings are reproducible: the
test recomputes the render timing independently and asserts the endpoint matches.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.adapters.fake import FakeTTS
from app.api.deps import get_repo, get_tts
from app.api.main import create_app
from app.api.repo import InMemoryRepository
from app.api.routers.scenes import _voice_map
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize
from app.render.audio.pipeline import render_scene_audio_with_timing


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


def auth_headers(client, email="timeline@example.com", password="salt-marsh-4"):
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


def _scene1(sample_fountain):
    graph = normalize(parse_fountain(sample_fountain))
    return next(s for s in graph.scenes if s.ordinal == 1)


def _expected_timing(sample_fountain):
    scene = _scene1(sample_fountain)
    _result, timing = asyncio.run(
        render_scene_audio_with_timing(scene, _voice_map(scene), FakeTTS(), seed=7)
    )
    return timing


def _shotlist_for_scene1():
    """Shots covering, in order: lines [1,3] (a spoken action + a spoken
    dialogue line), line [2] (a parenthetical — skipped in the render, so this
    shot maps to nothing and is omitted), and line [4] (a spoken action)."""
    common = {
        "axis_side": "a",
        "lens_mm": 50,
        "camera_height": "eye",
        "movement": "static",
        "eyeline": "none",
    }
    return {
        "scene_ordinal": 1,
        "action_axis": "MARA to lighthouse door",
        "shots": [
            {"ordinal": 1, "size": "ws", "subjects": ["MARA"],
             "covers_lines": [1, 3], "intent": "Establish Mara in the rain", **common},
            {"ordinal": 2, "size": "cu", "subjects": ["MARA"],
             "covers_lines": [2], "intent": "Insert on a beat with no audio", **common},
            {"ordinal": 3, "size": "ms", "subjects": ["MARA"],
             "covers_lines": [4], "intent": "Mara pounds the door", **common},
        ],
    }


def test_timeline_404_before_render(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    r = client.get(f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers)
    assert r.status_code == 404


def test_timeline_missing_scene_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    r = client.get(f"/api/v1/projects/{project_id}/scenes/99/timeline", headers=headers)
    assert r.status_code == 404


def test_timeline_owner_isolation(client, sample_fountain):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = make_project(client, headers_a)
    upload_script(client, headers_a, project_id, sample_fountain)
    client.post(f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers_a)
    r = client.get(f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers_b)
    assert r.status_code == 404


def test_timeline_audio_lanes_match_render(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)

    rendered = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
    )
    assert rendered.status_code == 201

    r = client.get(f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers)
    assert r.status_code == 200
    body = r.json()

    expected = _expected_timing(sample_fountain)
    assert body["scene_ordinal"] == 1
    assert body["duration_ms"] == expected.duration_ms == rendered.json()["duration_ms"]

    # Single scene marker at 0 with the slugline.
    assert body["markers"] == [
        {"scene_ordinal": 1, "start_ms": 0, "slugline": "EXT. HARBOR TOWN - NIGHT"}
    ]

    # Dialogue lane: every placed spoken clip in order, exact onsets/durations.
    assert [c["line_ordinal"] for c in body["dialogue"]] == [
        c.line_ordinal for c in expected.clips
    ]
    assert [c["start_ms"] for c in body["dialogue"]] == [
        c.start_ms for c in expected.clips
    ]
    assert [c["duration_ms"] for c in body["dialogue"]] == [
        c.duration_ms for c in expected.clips
    ]
    # Emotion carried through (MARA's line is 'whispering' in the fixture).
    mara = next(c for c in body["dialogue"] if c["character"] == "MARA")
    assert mara["emotion"] == "whispering"

    # Ambience lane: one span per tag, each covering the whole scene.
    assert {s["tag"] for s in body["ambience"]} == set(expected.ambience_tags)
    for span in body["ambience"]:
        assert span["start_ms"] == 0
        assert span["duration_ms"] == expected.duration_ms

    # SFX lane: name + onset, matching the render pass exactly.
    assert [(m["name"], m["at_ms"]) for m in body["sfx"]] == [
        (m.name, m.at_ms) for m in expected.sfx
    ]


def test_timeline_visual_lane_maps_shots_to_line_timings(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=_shotlist_for_scene1(),
        headers=headers,
    )
    client.post(f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers)

    body = client.get(
        f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers
    ).json()

    # line->(start,end) from the dialogue lane the endpoint returned.
    spans = {c["line_ordinal"]: (c["start_ms"], c["start_ms"] + c["duration_ms"])
             for c in body["dialogue"]}
    l1, l3, l4 = spans[1], spans[3], spans[4]

    visual = body["visual"]
    # Shot 2 covered only line 2 (a parenthetical, never placed) => omitted.
    assert [v["shot_ordinal"] for v in visual] == [1, 3]

    shot1 = visual[0]
    assert shot1["size"] == "ws"
    assert shot1["subjects"] == ["MARA"]
    # Spans from the earliest covered onset to the latest covered end.
    assert shot1["start_ms"] == min(l1[0], l3[0])
    assert shot1["duration_ms"] == max(l1[1], l3[1]) - min(l1[0], l3[0])

    shot3 = visual[1]
    assert shot3["shot_ordinal"] == 3
    assert shot3["start_ms"] == l4[0]
    assert shot3["duration_ms"] == l4[1] - l4[0]


def test_timeline_visual_lane_empty_without_shotlist(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    client.post(f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers)
    body = client.get(
        f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers
    ).json()
    assert body["visual"] == []
    assert body["dialogue"]  # audio lanes still present
