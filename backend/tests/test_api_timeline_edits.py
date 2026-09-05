"""API tests: POST .../timeline/edits — the timeline as an IR editor.

get_tts is overridden with FakeTTS so nothing hits the network or spends
credits. FakeTTS durations are deterministic (words * 60 ms), so a pacing edit
has an exactly predictable effect on the re-rendered scene length.
"""

import pytest
from fastapi.testclient import TestClient

from app.adapters.fake import FakeTTS
from app.api.deps import get_repo, get_tts
from app.api.main import create_app
from app.api.repo import InMemoryRepository

EDITS_URL = "/api/v1/projects/{p}/scenes/1/timeline/edits"


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


def auth_headers(client, email="edits@example.com", password="salt-marsh-4"):
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


def setup_scene(client, headers, sample_fountain, render=True):
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    if render:
        r = client.post(
            f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
        )
        assert r.status_code == 201
    return project_id


def _shotlist_for_scene1():
    """Scene 1 is: action(1), parenthetical(2), MARA dialogue(3), action(4)."""
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
            {"ordinal": 1, "size": "ws", "subjects": ["MARA"], "covers_lines": [1],
             "intent": "Establish Mara in the rain", **common},
            {"ordinal": 2, "size": "ms", "subjects": ["MARA"], "covers_lines": [3, 4],
             "intent": "Mara at the door", **common},
        ],
    }


# -- gating ------------------------------------------------------------------


def test_edits_require_ownership(client, sample_fountain):
    headers_a = auth_headers(client, email="a@example.com")
    headers_b = auth_headers(client, email="b@example.com")
    project_id = setup_scene(client, headers_a, sample_fountain)
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{"op": "set_scene_pacing", "pacing": 0.8}]},
        headers=headers_b,
    )
    assert r.status_code == 404


def test_edits_require_authentication(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{"op": "set_scene_pacing", "pacing": 0.8}]},
    )
    assert r.status_code == 401


def test_edits_require_rights_attestation(client, repo, sample_fountain):
    # POST /projects rejects rights_attested=False outright, so exercise the
    # gate the way the audio tests do: flip the flag on the stored record.
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain, render=False)
    repo.get_project(project_id).rights_attested = False
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{"op": "set_scene_pacing", "pacing": 0.8}]},
        headers=headers,
    )
    assert r.status_code == 403


def test_edits_on_a_missing_scene_404(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain, render=False)
    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/99/timeline/edits",
        json={"edits": [{"op": "set_scene_pacing", "pacing": 0.8}]},
        headers=headers,
    )
    assert r.status_code == 404


# -- reassign_line_character -------------------------------------------------


def test_reassign_line_updates_lane_graph_and_marks_audio_stale(
    client, repo, sample_fountain
):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)

    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [
            {"op": "reassign_line_character", "line_ordinal": 3, "character_name": "TOM"}
        ]},
        headers=headers,
    )
    assert r.status_code == 200
    body = r.json()

    # The lane shows the new speaker immediately...
    clip = next(c for c in body["dialogue"] if c["line_ordinal"] == 3)
    assert clip["character"] == "TOM"
    # ...and admits the WAV no longer matches.
    assert body["stale"] is True
    assert "reassigned from MARA to TOM" in body["stale_reasons"][0]

    # The IR itself changed, not just the projection.
    graph = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers).json()
    scene1 = next(s for s in graph["scenes"] if s["ordinal"] == 1)
    line = next(l for l in scene1["lines"] if l["ordinal"] == 3)
    assert line["character_name"] == "TOM"
    assert line["attribution_source"] == "manual"
    counts = {c["canonical_name"]: c["line_count"] for c in graph["characters"]}
    assert counts["TOM"] == 4 and counts["MARA"] == 3

    # GET /timeline agrees with what the POST returned.
    later = client.get(
        f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers
    ).json()
    assert later["stale"] is True
    assert next(c for c in later["dialogue"] if c["line_ordinal"] == 3)["character"] == "TOM"

    # And the served WAV advertises the mismatch rather than hiding it.
    audio = client.get(f"/api/v1/projects/{project_id}/scenes/1/audio", headers=headers)
    assert audio.headers["x-render-stale"] == "true"


def test_reassign_to_unknown_character_is_422_not_500(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [
            {"op": "reassign_line_character", "line_ordinal": 3, "character_name": "MARRA"}
        ]},
        headers=headers,
    )
    assert r.status_code == 422
    assert "unknown character" in r.json()["detail"]


def test_bad_line_ordinal_is_422_and_names_the_failing_edit(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [
            {"op": "set_scene_pacing", "pacing": 0.9},
            {"op": "reassign_line_character", "line_ordinal": 99, "character_name": "TOM"},
        ]},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["detail"].startswith("edit 1:")

    # Nothing from the batch stuck: the earlier pacing op rolled back with it.
    body = client.get(
        f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers
    ).json()
    assert body["settings"]["pacing"] == 1.0
    assert body["stale"] is False


def test_unknown_op_is_422(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{"op": "delete_everything"}]},
        headers=headers,
    )
    assert r.status_code == 422


# -- set_line_emotion --------------------------------------------------------


def test_set_line_emotion_shows_on_the_lane_and_reaches_the_next_render(
    client, sample_fountain
):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)
    body = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{"op": "set_line_emotion", "line_ordinal": 3, "emotion": "angry"}]},
        headers=headers,
    ).json()
    clip = next(c for c in body["dialogue"] if c["line_ordinal"] == 3)
    assert clip["emotion"] == "angry"  # was 'whispering' from the parenthetical
    assert body["stale"] is True

    # A re-render clears staleness and keeps the edited emotion.
    client.post(f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers)
    after = client.get(
        f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers
    ).json()
    assert after["stale"] is False and after["stale_reasons"] == []
    assert next(c for c in after["dialogue"] if c["line_ordinal"] == 3)["emotion"] == "angry"


def test_unknown_emotion_is_422(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{"op": "set_line_emotion", "line_ordinal": 3, "emotion": "livid"}]},
        headers=headers,
    )
    assert r.status_code == 422


# -- render settings ---------------------------------------------------------


def test_pacing_edit_shortens_the_next_render(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)
    before = client.get(
        f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers
    ).json()

    edited = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{"op": "set_scene_pacing", "pacing": 0.5}]},
        headers=headers,
    ).json()
    assert edited["settings"]["pacing"] == 0.5
    assert edited["stale"] is True
    # Pacing cannot be honored without re-rendering, so the lanes have not moved.
    assert edited["duration_ms"] == before["duration_ms"]

    rendered = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
    ).json()
    assert rendered["duration_ms"] < before["duration_ms"]

    after = client.get(
        f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers
    ).json()
    assert after["stale"] is False
    assert after["duration_ms"] == rendered["duration_ms"]
    # Gaps shrank; the spoken clips themselves are untouched TTS output.
    assert [c["duration_ms"] for c in after["dialogue"]] == [
        c["duration_ms"] for c in before["dialogue"]
    ]
    assert after["dialogue"][-1]["start_ms"] < before["dialogue"][-1]["start_ms"]


def test_ambience_duck_edit_changes_the_rendered_bytes(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)
    first = client.get(f"/api/v1/projects/{project_id}/scenes/1/audio", headers=headers)

    client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{"op": "set_ambience_duck", "depth": 0.0}]},
        headers=headers,
    )
    client.post(f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers)
    second = client.get(f"/api/v1/projects/{project_id}/scenes/1/audio", headers=headers)

    assert second.content != first.content
    assert second.headers["x-render-stale"] == "false"


def test_pacing_out_of_range_is_422(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain)
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{"op": "set_scene_pacing", "pacing": 40}]},
        headers=headers,
    )
    assert r.status_code == 422


# -- shot ops ----------------------------------------------------------------


def test_insert_reaction_shot_lands_on_the_visual_lane(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain, render=False)
    client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=_shotlist_for_scene1(),
        headers=headers,
    )
    client.post(f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers)

    body = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{
            "op": "insert_shot",
            "after_ordinal": 1,
            "shot": {"size": "cu", "subjects": ["TOM"], "covers_lines": [3],
                     "intent": "Reaction on the ultimatum"},
        }]},
        headers=headers,
    ).json()

    # Inserted between the two existing shots and renumbered to 1..3.
    assert [v["shot_ordinal"] for v in body["visual"]] == [1, 2, 3]
    reaction = body["visual"][1]
    assert reaction["size"] == "cu" and reaction["subjects"] == ["TOM"]
    line3 = next(c for c in body["dialogue"] if c["line_ordinal"] == 3)
    assert reaction["start_ms"] == line3["start_ms"]
    # A shot-list edit does not invalidate the audio.
    assert body["stale"] is False

    shots = client.get(
        f"/api/v1/projects/{project_id}/scenes/1/shots", headers=headers
    ).json()["shots"]
    assert [s["ordinal"] for s in shots] == [1, 2, 3]
    assert shots[1]["intent"] == "Reaction on the ultimatum"


def test_set_shot_coverage_moves_the_visual_clip(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain, render=False)
    client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=_shotlist_for_scene1(),
        headers=headers,
    )
    client.post(f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers)

    body = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [
            {"op": "set_shot_coverage", "shot_ordinal": 2, "covers_lines": [4]}
        ]},
        headers=headers,
    ).json()
    line4 = next(c for c in body["dialogue"] if c["line_ordinal"] == 4)
    shot2 = next(v for v in body["visual"] if v["shot_ordinal"] == 2)
    assert shot2["start_ms"] == line4["start_ms"]
    assert shot2["duration_ms"] == line4["duration_ms"]


def test_shot_coverage_referencing_a_missing_line_is_422(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain, render=False)
    client.post(
        f"/api/v1/projects/{project_id}/scenes/1/shotlist",
        json=_shotlist_for_scene1(),
        headers=headers,
    )
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [
            {"op": "set_shot_coverage", "shot_ordinal": 2, "covers_lines": [77]}
        ]},
        headers=headers,
    )
    assert r.status_code == 422


def test_insert_shot_without_a_shot_list_is_422(client, sample_fountain):
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain, render=False)
    r = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [{
            "op": "insert_shot",
            "after_ordinal": None,
            "shot": {"size": "cu", "subjects": ["MARA"], "covers_lines": [3],
                     "intent": "Reaction"},
        }]},
        headers=headers,
    )
    assert r.status_code == 422
    assert "no shot list" in r.json()["detail"]


# -- editing before a render -------------------------------------------------


def test_edits_apply_before_any_render_and_return_estimated_lanes(client, sample_fountain):
    """An edit made before any render is visible immediately, on planned lanes.

    This is what makes the timeline editor usable on a deployment that has never
    spent TTS credits: the lanes come from the script, and ``timing_source``
    keeps them from being mistaken for measurements.
    """
    headers = auth_headers(client)
    project_id = setup_scene(client, headers, sample_fountain, render=False)
    body = client.post(
        EDITS_URL.format(p=project_id),
        json={"edits": [
            {"op": "reassign_line_character", "line_ordinal": 3, "character_name": "TOM"},
            {"op": "set_scene_pacing", "pacing": 0.8},
        ]},
        headers=headers,
    ).json()

    assert body["timing_source"] == "estimated"
    assert body["duration_ms"] > 0
    # The edit shows on the lane without a render having happened.
    assert next(c for c in body["dialogue"] if c["line_ordinal"] == 3)["character"] == "TOM"
    # No shot list was uploaded, so the visual lane has nothing to place.
    assert body["visual"] == []
    assert body["markers"][0]["slugline"] == "EXT. HARBOR TOWN - NIGHT"
    assert body["settings"]["pacing"] == 0.8
    assert body["stale"] is False  # nothing rendered, so nothing to invalidate

    rendered = client.post(
        f"/api/v1/projects/{project_id}/scenes/1/render/audio", headers=headers
    )
    assert rendered.status_code == 201
    after = client.get(
        f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers
    ).json()
    assert next(c for c in after["dialogue"] if c["line_ordinal"] == 3)["character"] == "TOM"
