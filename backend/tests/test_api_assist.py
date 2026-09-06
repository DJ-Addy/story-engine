"""API tests: POST .../scenes/{ordinal}/assist — the edit assistant over HTTP.

Nothing here reaches a provider. ``get_llm`` is overridden with a scripted fake
whose canned answer each test writes, which is also what lets these tests assert
the *contract* around the call: that the cost governor is consulted before the
model is, that a hallucinated op never reaches the response, and that the batch
the assistant hands back is one the real edits endpoint accepts.

The one test that does not override the provider is the 503: a deployment with
no Google Cloud project must answer with the variables to set, and that path
only exists when the real dependency runs.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.adapters.fake import FakeLLM, FakeTTS
from app.analytics.events import AnalyticsEvent
from app.api.deps import get_llm, get_repo, get_tts
from app.api.main import create_app
from app.api.repo import InMemoryRepository
from app.api.routers.analytics import get_analytics_recorder

ASSIST_URL = "/api/v1/projects/{p}/scenes/{s}/assist"
EDITS_URL = "/api/v1/projects/{p}/scenes/{s}/timeline/edits"
TIMELINE_URL = "/api/v1/projects/{p}/scenes/{s}/timeline"

# Scene 2 of the fixture: TOM on lines 2, 5 and 8, MARA on 3 and 6.
SCENE = 2


class ScriptedLLM(FakeLLM):
    """A FakeLLM whose answer each test writes, and that remembers its prompts.

    The prompt record is the only way to prove the scene actually reached the
    model — an assistant that proposes ``line_ordinal: 7`` without having been
    shown line 7 is guessing, however good the answer looks.
    """

    def __init__(self) -> None:
        super().__init__(response="{}")
        self.prompts: list[tuple[str, str, dict]] = []

    def answers(self, reply: str, edits: list[dict] | None = None) -> None:
        self._response = json.dumps({"reply": reply, "edits": edits or []})

    def answers_raw(self, text: str) -> None:
        self._response = text

    async def complete(self, system: str, user: str, params: dict):
        self.prompts.append((system, user, params))
        return await super().complete(system, user, params)


class CapturingRecorder:
    """Stands in for the ClickHouse recorder; keeps what the route emitted."""

    def __init__(self) -> None:
        self.events: list[AnalyticsEvent] = []

    def record(self, events) -> int:
        captured = list(events)
        self.events.extend(captured)
        return len(captured)


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def llm():
    return ScriptedLLM()


@pytest.fixture
def recorder():
    return CapturingRecorder()


@pytest.fixture
def app(repo, llm, recorder):
    application = create_app()
    application.dependency_overrides[get_repo] = lambda: repo
    application.dependency_overrides[get_llm] = lambda: llm
    application.dependency_overrides[get_tts] = lambda: FakeTTS()
    application.dependency_overrides[get_analytics_recorder] = lambda: recorder
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def auth_headers(client, email="assist@example.com", password="lantern-oil-9"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def make_project(client, headers):
    r = client.post(
        "/api/v1/projects",
        json={"title": "The Lighthouse Wager", "rights_attested": True},
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


@pytest.fixture
def project(client, sample_fountain):
    headers = auth_headers(client)
    project_id = make_project(client, headers)
    upload_script(client, headers, project_id, sample_fountain)
    return project_id, headers


def ask(client, project_id, headers, message="Tom sounds too warm here.", **body):
    return client.post(
        ASSIST_URL.format(p=project_id, s=SCENE),
        json={"message": message, **body},
        headers=headers,
    )


def cost_events(recorder):
    return [e for e in recorder.events if e.TABLE == "cost_events"]


# --------------------------------------------------------------------------- #
# Configuration: the answer a credential-less deployment gets
# --------------------------------------------------------------------------- #
class TestNotConfigured:
    def test_503_names_the_environment_variables_to_set(
        self, app, client, project, monkeypatch
    ):
        project_id, headers = project
        # The real dependency, on a machine with nothing configured.
        app.dependency_overrides.pop(get_llm)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)

        r = ask(client, project_id, headers)

        assert r.status_code == 503
        detail = r.json()["detail"]
        assert "GOOGLE_CLOUD_PROJECT" in detail
        assert "GOOGLE_APPLICATION_CREDENTIALS" in detail

    def test_the_503_is_about_configuration_and_nothing_else(self, monkeypatch):
        """A configured project resolves the adapter instead of raising.

        Asserted on the dependency rather than through a request: with a project
        set, the route would go on to call Gemini for real, and no test in this
        suite is allowed to reach a provider or spend a cent.
        """
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "story-engine-demo")
        assert get_llm().name == "gemini"


# --------------------------------------------------------------------------- #
# Gates
# --------------------------------------------------------------------------- #
class TestGates:
    def test_unattested_rights_are_refused_before_the_model_is_called(
        self, client, repo, project, llm
    ):
        # POST /projects rejects rights_attested=False outright, so the gate is
        # exercised the way the render and edit tests do: flip the stored flag.
        project_id, headers = project
        repo.get_project(project_id).rights_attested = False

        r = ask(client, project_id, headers)

        assert r.status_code == 403
        assert "Rights not attested" in r.json()["detail"]
        assert llm.prompts == []

    def test_an_unknown_scene_is_a_404(self, client, project):
        project_id, headers = project
        r = client.post(
            ASSIST_URL.format(p=project_id, s=99),
            json={"message": "anything"},
            headers=headers,
        )
        assert r.status_code == 404

    def test_another_users_project_is_invisible(self, client, project):
        project_id, _ = project
        stranger = auth_headers(client, email="stranger@example.com")
        r = ask(client, project_id, stranger)
        assert r.status_code == 404

    def test_an_empty_message_is_rejected_by_the_schema(self, client, project, llm):
        project_id, headers = project
        r = ask(client, project_id, headers, message="")
        assert r.status_code == 422
        assert llm.prompts == []


# --------------------------------------------------------------------------- #
# The cost governor
# --------------------------------------------------------------------------- #
class TestCostGovernor:
    def test_the_guard_runs_before_the_paid_call(self, client, repo, project, llm):
        project_id, headers = project
        repo.get_project(project_id).cost_cap_cents = 0

        r = ask(client, project_id, headers)

        assert r.status_code == 402
        assert "Cost cap exceeded" in r.json()["detail"]
        # The point of the guard: no prompt was ever sent.
        assert llm.prompts == []

    def test_a_refusal_is_recorded_as_a_cost_event(
        self, client, repo, project, recorder
    ):
        project_id, headers = project
        repo.get_project(project_id).cost_cap_cents = 0

        assert ask(client, project_id, headers).status_code == 402

        events = cost_events(recorder)
        assert len(events) == 1
        assert events[0].operation == "assist"
        assert events[0].allowed is False
        assert events[0].scene_ordinal == SCENE
        # Signed headroom is how far over the cap the request would have gone.
        assert events[0].headroom_cents < 0

    def test_an_allowed_turn_is_recorded_and_charged_to_the_project(
        self, client, repo, project, recorder, llm
    ):
        project_id, headers = project
        llm.answers("Nothing to change here.")

        r = ask(client, project_id, headers)

        assert r.status_code == 200
        estimated = r.json()["estimated_cost_cents"]
        assert estimated >= 1
        assert repo.get_project(project_id).cost_spent_cents == estimated

        events = cost_events(recorder)
        assert [e.allowed for e in events] == [True]
        assert events[0].estimated_cents == estimated

    def test_the_estimate_prices_the_prompt_that_was_actually_sent(
        self, client, project, llm
    ):
        project_id, headers = project
        llm.answers("Nothing to change here.")

        estimated = ask(client, project_id, headers).json()["estimated_cost_cents"]

        system, user, _ = llm.prompts[0]
        assert estimated == llm.estimate_cost_cents(len(system) + len(user))


# --------------------------------------------------------------------------- #
# The proposal
# --------------------------------------------------------------------------- #
class TestProposal:
    def test_the_scene_reaches_the_model(self, client, project, llm):
        project_id, headers = project
        llm.answers("Looking.")

        ask(client, project_id, headers)

        _, user, params = llm.prompts[0]
        assert "2 | dialogue | TOM | - | You shouldn't be out in this." in user
        assert "Tom sounds too warm here." in user
        # Structured output is requested, not assumed (the parser stays tolerant).
        assert params["responseMimeType"] == "application/json"

    def test_prose_and_a_validated_batch_come_back_together(
        self, client, project, llm
    ):
        project_id, headers = project
        llm.answers(
            "Line 5 is the cold one — try 'serious'.",
            [{"op": "set_line_emotion", "line_ordinal": 5, "emotion": "serious"}],
        )

        data = ask(client, project_id, headers).json()

        assert data["reply"] == "Line 5 is the cold one — try 'serious'."
        assert len(data["edits"]) == 1
        assert data["edits"][0]["edit"] == {
            "op": "set_line_emotion",
            "line_ordinal": 5,
            "emotion": "serious",
        }
        assert data["edits"][0]["summary"] == (
            "Set line 5 delivery to serious (was neutral)"
        )
        assert data["provider"] == "fake-llm"

    def test_a_hallucinated_op_is_dropped_and_named_rather_than_shown(
        self, client, project, llm
    ):
        project_id, headers = project
        llm.answers(
            "Two changes.",
            [
                {"op": "set_line_emotion", "line_ordinal": 40, "emotion": "sad"},
                {"op": "set_line_emotion", "line_ordinal": 5, "emotion": "serious"},
            ],
        )

        data = ask(client, project_id, headers).json()

        assert [e["edit"]["line_ordinal"] for e in data["edits"]] == [5]
        assert any("line 40 not found" in d for d in data["dropped"])

    def test_a_reply_that_is_not_json_still_answers_the_user(
        self, client, project, llm
    ):
        project_id, headers = project
        llm.answers_raw("I cannot rewrite dialogue — only who says it and how.")

        data = ask(client, project_id, headers).json()

        assert data["reply"].startswith("I cannot rewrite dialogue")
        assert data["edits"] == []
        assert data["dropped"]

    def test_the_conversation_so_far_is_carried_by_the_client(
        self, client, project, llm
    ):
        project_id, headers = project
        llm.answers("Doing that now.")

        ask(
            client,
            project_id,
            headers,
            message="do it",
            history=[
                {"role": "user", "content": "is line 5 too warm?"},
                {"role": "assistant", "content": "I would try 'serious'."},
            ],
        )

        _, user, _ = llm.prompts[0]
        assert "user: is line 5 too warm?" in user
        assert "assistant: I would try 'serious'." in user


# --------------------------------------------------------------------------- #
# The round trip: propose -> apply -> undo, all through the real endpoints
# --------------------------------------------------------------------------- #
class TestRoundTrip:
    def _propose(self, client, project_id, headers, llm, edits):
        llm.answers("Here is what I would do.", edits)
        r = ask(client, project_id, headers)
        assert r.status_code == 200
        return r.json()

    def test_a_proposal_applies_through_the_existing_edits_endpoint(
        self, client, project, llm
    ):
        project_id, headers = project
        data = self._propose(
            client,
            project_id,
            headers,
            llm,
            [
                {
                    "op": "reassign_line_character",
                    "line_ordinal": 5,
                    "character_name": "MARA",
                },
                {"op": "set_scene_pacing", "pacing": 0.85},
            ],
        )

        applied = client.post(
            EDITS_URL.format(p=project_id, s=SCENE),
            json={"edits": [e["edit"] for e in data["edits"]]},
            headers=headers,
        )
        assert applied.status_code == 200
        timeline = applied.json()
        assert timeline["settings"]["pacing"] == 0.85
        speakers = {c["line_ordinal"]: c["character"] for c in timeline["dialogue"]}
        assert speakers[5] == "MARA"

    def test_the_undo_batch_puts_it_back(self, client, project, llm):
        project_id, headers = project
        before = client.get(
            TIMELINE_URL.format(p=project_id, s=SCENE), headers=headers
        ).json()

        data = self._propose(
            client,
            project_id,
            headers,
            llm,
            [
                {"op": "set_line_emotion", "line_ordinal": 5, "emotion": "serious"},
                {"op": "set_ambience_duck", "depth": 0.9},
            ],
        )
        assert data["undo_blocked_by"] is None
        assert data["undo_summary"] == [
            "Duck ambience 50% under speech (was 90%)",
            "Set line 5 delivery to neutral (was serious)",
        ]

        client.post(
            EDITS_URL.format(p=project_id, s=SCENE),
            json={"edits": [e["edit"] for e in data["edits"]]},
            headers=headers,
        )
        undone = client.post(
            EDITS_URL.format(p=project_id, s=SCENE),
            json={"edits": data["undo"]},
            headers=headers,
        )

        assert undone.status_code == 200
        after = undone.json()
        assert after["settings"] == before["settings"]
        assert [(c["line_ordinal"], c["character"], c["emotion"]) for c in after["dialogue"]] == [
            (c["line_ordinal"], c["character"], c["emotion"]) for c in before["dialogue"]
        ]

    def test_an_inserted_shot_is_proposed_but_reported_as_not_undoable(
        self, client, project, llm
    ):
        project_id, headers = project
        shotlist = {
            "scene_ordinal": SCENE,
            "action_axis": "TOM to MARA",
            "shots": [
                {
                    "ordinal": 1,
                    "size": "ws",
                    "subjects": ["TOM", "MARA"],
                    "axis_side": "a",
                    "lens_mm": 35,
                    "camera_height": "eye",
                    "movement": "static",
                    "eyeline": "none",
                    "covers_lines": [1, 2],
                    "intent": "The door opens",
                }
            ],
        }
        assert (
            client.post(
                f"/api/v1/projects/{project_id}/scenes/{SCENE}/shotlist",
                json=shotlist,
                headers=headers,
            ).status_code
            == 201
        )

        data = self._propose(
            client,
            project_id,
            headers,
            llm,
            [
                {
                    "op": "insert_shot",
                    "after_ordinal": 1,
                    "shot": {
                        "size": "cu",
                        "subjects": ["MARA"],
                        "covers_lines": [3],
                        "intent": "Her answer lands",
                    },
                }
            ],
        )

        assert data["edits"][0]["summary"] == (
            "Insert a CU of MARA after shot 1, covering line 3"
        )
        assert data["undo"] == []
        assert "no op that removes a shot" in data["undo_blocked_by"]

        applied = client.post(
            EDITS_URL.format(p=project_id, s=SCENE),
            json={"edits": [e["edit"] for e in data["edits"]]},
            headers=headers,
        )
        assert applied.status_code == 200
        assert [v["shot_ordinal"] for v in applied.json()["visual"]] == [1, 2]

    def test_a_batch_is_all_or_nothing_and_the_assistant_only_offers_ones_that_land(
        self, client, project, llm
    ):
        """Every op the model produced is applicable, or it is not in the batch.

        The negative half is the contract: a batch that still contained the bad
        op would be rejected whole, so an assistant that surfaced it would be
        offering a button that always fails.
        """
        project_id, headers = project
        data = self._propose(
            client,
            project_id,
            headers,
            llm,
            [
                {"op": "set_line_emotion", "line_ordinal": 5, "emotion": "serious"},
                {"op": "set_line_emotion", "line_ordinal": 4, "emotion": "calm"},
            ],
        )
        assert [e["edit"]["line_ordinal"] for e in data["edits"]] == [5]

        assert (
            client.post(
                EDITS_URL.format(p=project_id, s=SCENE),
                json={"edits": [e["edit"] for e in data["edits"]]},
                headers=headers,
            ).status_code
            == 200
        )
        # The dropped op, sent anyway, is what the user was spared.
        refused = client.post(
            EDITS_URL.format(p=project_id, s=SCENE),
            json={
                "edits": [
                    {"op": "set_line_emotion", "line_ordinal": 5, "emotion": "urgent"},
                    {"op": "set_line_emotion", "line_ordinal": 4, "emotion": "calm"},
                ]
            },
            headers=headers,
        )
        assert refused.status_code == 422
        timeline = client.get(
            TIMELINE_URL.format(p=project_id, s=SCENE), headers=headers
        ).json()
        emotions = {c["line_ordinal"]: c["emotion"] for c in timeline["dialogue"]}
        assert emotions[5] == "serious"  # the refused batch changed nothing
