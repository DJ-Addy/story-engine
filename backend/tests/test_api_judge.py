"""API tests for the judge endpoints: voice-fit casting and animatic quality.

Mirrors tests/test_api_scenes.py: a fresh in-memory repo per client, bearer
auth, and a project seeded with the sample screenplay.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_repo
from app.api.main import create_app
from app.api.repo import InMemoryRepository

SOFT = {"id": "soft1", "name": "Bella", "tags": ["female", "soft", "conversational"]}
STRONG = {"id": "strong1", "name": "Domi", "tags": ["female", "expressive", "strong"]}
NARR = {"id": "narr1", "name": "Guy", "tags": ["male", "narrator", "en-US"]}
POOL = [SOFT, STRONG, NARR]


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
    project_id = client.post("/api/v1/projects", json=payload, headers=headers).json()["id"]
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
                "ordinal": 1, "size": "cu", "subjects": ["MARA"], "axis_side": "a",
                "lens_mm": 50, "camera_height": "eye", "movement": "static",
                "eyeline": "left", "covers_lines": [1, 2, 3], "intent": "Close on Mara",
            },
            {
                "ordinal": 2, "size": "cu", "subjects": ["MARA"], "axis_side": "b",
                "lens_mm": 50, "camera_height": "eye", "movement": "static",
                "eyeline": "right", "covers_lines": [4], "intent": "Reverse, wrong side",
            },
        ],
    }


def clean_shotlist(scene_ordinal=1):
    """Same coverage as violating_shotlist but single axis and varied sizes."""
    return {
        "scene_ordinal": scene_ordinal,
        "action_axis": "MARA to lighthouse door",
        "shots": [
            {
                "ordinal": 1, "size": "ws", "subjects": ["MARA"], "axis_side": "a",
                "lens_mm": 24, "camera_height": "eye", "movement": "static",
                "eyeline": "none", "covers_lines": [1, 2, 3], "intent": "Establish",
            },
            {
                "ordinal": 2, "size": "cu", "subjects": ["MARA"], "axis_side": "a",
                "lens_mm": 50, "camera_height": "eye", "movement": "static",
                "eyeline": "left", "covers_lines": [4], "intent": "Close on Mara",
            },
        ],
    }


# --- /judge/voices --------------------------------------------------------- #
class TestJudgeVoices:
    def test_returns_structured_casting_result(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        body = {"casting": {"MARA": SOFT, "TOM": NARR}, "available_voices": POOL}

        r = client.post(
            f"/api/v1/projects/{project_id}/judge/voices", json=body, headers=headers
        )
        assert r.status_code == 200
        data = r.json()
        assert 0.0 <= data["overall_score"] <= 1.0
        assert data["rationale"]
        assert {c["character"] for c in data["characters"]} == {"MARA", "TOM"}
        for c in data["characters"]:
            assert 0.0 <= c["score"] <= 1.0
            assert "findings" in c and "suggestions" in c
            assert c["voice_id"] in {"soft1", "narr1"}

    def test_uncast_speaking_character_reported(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        body = {"casting": {"MARA": SOFT}}
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/voices", json=body, headers=headers
        )
        assert r.status_code == 200
        assert "TOM" in r.json()["uncast_characters"]

    def test_empty_casting_is_422(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/voices",
            json={"casting": {}},
            headers=headers,
        )
        assert r.status_code == 422

    def test_404_when_no_script(self, client):
        headers = auth_headers(client)
        project_id = client.post(
            "/api/v1/projects",
            json={"title": "Empty", "rights_attested": True},
            headers=headers,
        ).json()["id"]
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/voices",
            json={"casting": {"MARA": SOFT}},
            headers=headers,
        )
        assert r.status_code == 404

    def test_owner_isolation(self, client, sample_fountain):
        headers_a = auth_headers(client, email="a@example.com")
        headers_b = auth_headers(client, email="b@example.com")
        project_id = project_with_script(client, headers_a, sample_fountain)
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/voices",
            json={"casting": {"MARA": SOFT}},
            headers=headers_b,
        )
        assert r.status_code == 404


# --- /judge/animatic ------------------------------------------------------- #
class TestJudgeAnimatic:
    def test_scores_stored_shotlists_and_flags_axis_cross(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        # Author a shot list for scene 1 (which trips AXIS_CROSS).
        assert (
            client.post(
                f"/api/v1/projects/{project_id}/scenes/1/shotlist",
                json=violating_shotlist(1),
                headers=headers,
            ).status_code
            == 201
        )

        r = client.post(f"/api/v1/projects/{project_id}/judge/animatic", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert 0.0 <= data["overall_score"] <= 1.0
        assert [s["scene_ordinal"] for s in data["scenes"]] == [1]
        assert "AXIS_CROSS" in {f["code"] for f in data["findings"]}

    def test_404_when_no_shotlists(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        r = client.post(f"/api/v1/projects/{project_id}/judge/animatic", headers=headers)
        assert r.status_code == 404

    def test_owner_isolation(self, client, sample_fountain):
        headers_a = auth_headers(client, email="a@example.com")
        headers_b = auth_headers(client, email="b@example.com")
        project_id = project_with_script(client, headers_a, sample_fountain)
        client.post(
            f"/api/v1/projects/{project_id}/scenes/1/shotlist",
            json=violating_shotlist(1),
            headers=headers_a,
        )
        r = client.post(f"/api/v1/projects/{project_id}/judge/animatic", headers=headers_b)
        assert r.status_code == 404


# --- /judge/rank/voices ---------------------------------------------------- #
class TestRankVoices:
    def test_returns_consistent_leaderboard(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        body = {
            "candidates": [
                {"label": "narrator-lead", "casting": {"MARA": NARR, "TOM": STRONG}},
                {"label": "soft-mara", "casting": {"MARA": SOFT, "TOM": NARR}},
            ],
            "available_voices": POOL,
        }
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/voices", json=body, headers=headers
        )
        assert r.status_code == 200
        data = r.json()
        assert {e["label"] for e in data["entries"]} == {"narrator-lead", "soft-mara"}
        # Ranks are contiguous 1..n, best first, and the winner is entry #1.
        assert [e["rank"] for e in data["entries"]] == [1, 2]
        scores = [e["overall_score"] for e in data["entries"]]
        assert scores == sorted(scores, reverse=True)
        assert data["winner"] == data["entries"][0]["label"]
        # Each entry carries the full VoiceFitResult it was ranked on.
        for e in data["entries"]:
            assert e["overall_score"] == e["result"]["overall_score"]
            assert {c["character"] for c in e["result"]["characters"]}

    def test_ties_break_by_label(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        casting = {"MARA": SOFT, "TOM": NARR}
        body = {
            "candidates": [
                {"label": "zebra", "casting": casting},
                {"label": "apple", "casting": casting},
            ],
            "available_voices": POOL,
        }
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/voices", json=body, headers=headers
        )
        assert r.status_code == 200
        data = r.json()
        assert [e["label"] for e in data["entries"]] == ["apple", "zebra"]
        assert data["winner"] == "apple"

    def test_empty_candidates_is_422(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/voices",
            json={"candidates": []},
            headers=headers,
        )
        assert r.status_code == 422

    def test_duplicate_labels_is_422(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        body = {
            "candidates": [
                {"label": "dup", "casting": {"MARA": SOFT}},
                {"label": "dup", "casting": {"MARA": STRONG}},
            ]
        }
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/voices", json=body, headers=headers
        )
        assert r.status_code == 422

    def test_404_when_no_script(self, client):
        headers = auth_headers(client)
        project_id = client.post(
            "/api/v1/projects",
            json={"title": "Empty", "rights_attested": True},
            headers=headers,
        ).json()["id"]
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/voices",
            json={"candidates": [{"label": "a", "casting": {"MARA": SOFT}}]},
            headers=headers,
        )
        assert r.status_code == 404

    def test_owner_isolation(self, client, sample_fountain):
        headers_a = auth_headers(client, email="a@example.com")
        headers_b = auth_headers(client, email="b@example.com")
        project_id = project_with_script(client, headers_a, sample_fountain)
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/voices",
            json={"candidates": [{"label": "a", "casting": {"MARA": SOFT}}]},
            headers=headers_b,
        )
        assert r.status_code == 404


# --- /judge/rank/animatic -------------------------------------------------- #
class TestRankAnimatic:
    def test_ranks_supplied_shotlist_variants(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        body = {
            "candidates": [
                {"label": "rough", "shotlists": [violating_shotlist(1)]},
                {"label": "clean", "shotlists": [clean_shotlist(1)]},
            ]
        }
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/animatic", json=body, headers=headers
        )
        assert r.status_code == 200
        data = r.json()
        # The clean variant (single axis, varied sizes) beats the axis-crossing one.
        assert data["winner"] == "clean"
        assert [e["label"] for e in data["entries"]] == ["clean", "rough"]
        assert [e["rank"] for e in data["entries"]] == [1, 2]
        assert data["entries"][0]["overall_score"] > data["entries"][1]["overall_score"]
        # Real per-variant scoring: the rough variant surfaces its AXIS_CROSS.
        rough = next(e for e in data["entries"] if e["label"] == "rough")
        assert "AXIS_CROSS" in {f["code"] for f in rough["result"]["findings"]}
        assert rough["result"]["scenes"][0]["scene_ordinal"] == 1

    def test_empty_candidates_is_422(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/animatic",
            json={"candidates": []},
            headers=headers,
        )
        assert r.status_code == 422

    def test_duplicate_labels_is_422(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        body = {
            "candidates": [
                {"label": "same", "shotlists": [clean_shotlist(1)]},
                {"label": "same", "shotlists": [violating_shotlist(1)]},
            ]
        }
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/animatic", json=body, headers=headers
        )
        assert r.status_code == 422

    def test_404_when_no_script(self, client):
        headers = auth_headers(client)
        project_id = client.post(
            "/api/v1/projects",
            json={"title": "Empty", "rights_attested": True},
            headers=headers,
        ).json()["id"]
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/animatic",
            json={"candidates": [{"label": "a", "shotlists": [clean_shotlist(1)]}]},
            headers=headers,
        )
        assert r.status_code == 404

    def test_owner_isolation(self, client, sample_fountain):
        headers_a = auth_headers(client, email="a@example.com")
        headers_b = auth_headers(client, email="b@example.com")
        project_id = project_with_script(client, headers_a, sample_fountain)
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/rank/animatic",
            json={"candidates": [{"label": "a", "shotlists": [clean_shotlist(1)]}]},
            headers=headers_b,
        )
        assert r.status_code == 404


class TestDecideCasting:
    """The judge makes a casting and writes it down, so the render can read it."""

    def test_decides_and_persists(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)

        r = client.post(
            f"/api/v1/projects/{project_id}/judge/casting",
            json={"available_voices": POOL},
            headers=headers,
        )
        assert r.status_code == 201, r.text
        entries = r.json()["entries"]
        assert entries, "expected at least a narrator"
        # Every id must be one we offered - never invented.
        assert {e["voice_id"] for e in entries} <= {v["id"] for v in POOL}

        # And it reads back, which is what the renderer relies on.
        got = client.get(
            f"/api/v1/projects/{project_id}/judge/casting", headers=headers
        )
        assert got.status_code == 200
        assert [e["voice_id"] for e in got.json()["entries"]] == [
            e["voice_id"] for e in entries
        ]

    def test_dry_run_decides_without_saving(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)

        r = client.post(
            f"/api/v1/projects/{project_id}/judge/casting",
            json={"available_voices": POOL, "persist": False},
            headers=headers,
        )
        assert r.status_code == 201
        assert r.json()["entries"]

        got = client.get(
            f"/api/v1/projects/{project_id}/judge/casting", headers=headers
        )
        assert got.json() is None, "a dry run must leave nothing behind"

    def test_no_casting_yet_is_null_not_an_error(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        r = client.get(
            f"/api/v1/projects/{project_id}/judge/casting", headers=headers
        )
        assert r.status_code == 200
        assert r.json() is None

    def test_empty_voice_pool_is_a_422(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/casting",
            json={"available_voices": []},
            headers=headers,
        )
        assert r.status_code == 422

    def test_404_when_no_script(self, client):
        headers = auth_headers(client)
        project_id = client.post(
            "/api/v1/projects",
            json={"title": "Empty", "rights_attested": True},
            headers=headers,
        ).json()["id"]
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/casting",
            json={"available_voices": POOL},
            headers=headers,
        )
        assert r.status_code == 404


# --- /judge/scorecard ------------------------------------------------------ #
def without_timestamp(body):
    """The scorecard minus its one moving part.

    Two requests are two moments, so the "Generated ... UTC" line can differ by
    a second between them. Everything else on the page is a pure function of
    the stored judgements, which is what these tests compare.
    """
    return [line for line in body.splitlines() if not line.startswith("Generated ")]


class TestScorecard:
    """One URL that explains, in prose, how every judge number was arrived at."""

    def test_explains_every_character_score_the_judge_gave(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        client.post(
            f"/api/v1/projects/{project_id}/judge/casting",
            json={"available_voices": POOL},
            headers=headers,
        )
        client.post(
            f"/api/v1/projects/{project_id}/scenes/1/shotlist",
            json=violating_shotlist(1),
            headers=headers,
        )

        r = client.get(f"/api/v1/projects/{project_id}/judge/scorecard", headers=headers)
        assert r.status_code == 200
        assert r.headers["content-type"] == "text/plain; charset=utf-8"
        body = r.text
        assert "JUDGE SCORECARD" in body and "Wager" in body

        # The same casting, scored through the public judge endpoint: every
        # character and every number on the page has to be one the judge
        # actually returned, not one the renderer arrived at on its own.
        saved = client.get(
            f"/api/v1/projects/{project_id}/judge/casting", headers=headers
        ).json()
        casting = {
            e["character"]: {"id": e["voice_id"], "name": e["voice_name"], "tags": []}
            for e in saved["entries"]
            if e["character"] is not None
        }
        fit = client.post(
            f"/api/v1/projects/{project_id}/judge/voices",
            json={"casting": casting},
            headers=headers,
        ).json()
        assert fit["characters"], "the sample script has speaking characters"
        for character in fit["characters"]:
            assert character["character"] in body
            assert f"{character['score']:.2f}" in body

        # And the animatic judgement's own finding is quoted under its axis.
        assert "AXIS_CROSS" in body
        assert "continuity" in body and "coverage" in body

    def test_untoned_part_reads_as_an_explanation_not_a_blank(
        self, client, sample_fountain
    ):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        client.post(
            f"/api/v1/projects/{project_id}/judge/casting",
            json={"available_voices": POOL},
            headers=headers,
        )
        saved = client.get(
            f"/api/v1/projects/{project_id}/judge/casting", headers=headers
        ).json()
        assert any(e["tone"] is None for e in saved["entries"]), "expected an untoned part"

        body = client.get(
            f"/api/v1/projects/{project_id}/judge/scorecard", headers=headers
        ).text
        assert "no tone proposed" in body
        assert "the text gave no signal" in " ".join(body.split())

    def test_partly_judged_project_is_200_with_a_not_yet_section(
        self, client, sample_fountain
    ):
        """A script with nothing judged is a normal state, not an error."""
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)

        r = client.get(f"/api/v1/projects/{project_id}/judge/scorecard", headers=headers)
        assert r.status_code == 200
        flat = " ".join(r.text.split())
        assert "CASTING — not decided yet" in flat
        assert "ANIMATIC — not judged yet" in flat
        # The section names the endpoint that would fill it in.
        assert "/judge/casting" in flat and "/shotlist" in flat

    def test_404_when_no_script(self, client):
        headers = auth_headers(client)
        project_id = client.post(
            "/api/v1/projects",
            json={"title": "Empty", "rights_attested": True},
            headers=headers,
        ).json()["id"]
        for path in ("scorecard", "scorecard.txt"):
            r = client.get(
                f"/api/v1/projects/{project_id}/judge/{path}", headers=headers
            )
            assert r.status_code == 404

    def test_txt_variant_is_offered_as_a_download(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        r = client.get(
            f"/api/v1/projects/{project_id}/judge/scorecard.txt", headers=headers
        )
        assert r.status_code == 200
        assert r.headers["content-disposition"] == (
            'attachment; filename="wager-scorecard.txt"'
        )

    def test_both_routes_serve_the_same_page(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        client.post(
            f"/api/v1/projects/{project_id}/judge/casting",
            json={"available_voices": POOL},
            headers=headers,
        )
        inline = client.get(
            f"/api/v1/projects/{project_id}/judge/scorecard", headers=headers
        )
        download = client.get(
            f"/api/v1/projects/{project_id}/judge/scorecard.txt", headers=headers
        )
        assert without_timestamp(inline.text) == without_timestamp(download.text)
        assert "content-disposition" not in inline.headers

    def test_repeated_requests_render_the_same_page(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        client.post(
            f"/api/v1/projects/{project_id}/judge/casting",
            json={"available_voices": POOL},
            headers=headers,
        )
        first = client.get(f"/api/v1/projects/{project_id}/judge/scorecard", headers=headers)
        second = client.get(f"/api/v1/projects/{project_id}/judge/scorecard", headers=headers)
        assert without_timestamp(first.text) == without_timestamp(second.text)

    def test_owner_isolation(self, client, sample_fountain):
        headers_a = auth_headers(client, email="a@example.com")
        headers_b = auth_headers(client, email="b@example.com")
        project_id = project_with_script(client, headers_a, sample_fountain)
        r = client.get(
            f"/api/v1/projects/{project_id}/judge/scorecard", headers=headers_b
        )
        assert r.status_code == 404
