"""API tests for the agent network: describing it, and watching it run.

The ADK is stubbed at the :class:`~app.adapters.adk.AdkApi` seam and Gemini is
never resolved, so nothing here imports ``google.adk``, reads a credential or
spends anything. Everything on the near side of that seam is real: the router,
the run driver, the context binding, the event translation, the SSE framing, and
the pipeline functions the specialists call - a ``judge_previz`` result in one
of these transcripts is a score the real animatic judge produced.

Mirrors tests/test_api_judge.py for the plumbing: a fresh in-memory repo per
client, bearer auth, a project seeded with the sample screenplay.
"""

import json

import pytest
from adk_fakes import Step, build_fake_adk
from fastapi.testclient import TestClient

from app.adapters.adk import AdkAgentRuntime
from app.adapters.base import TerminalProviderError
from app.api.deps import get_repo
from app.api.main import create_app
from app.api.repo import InMemoryRepository
from app.api.routers import agent as agent_router

SPECIALISTS = [
    "script_analyst",
    "casting_director",
    "shot_designer",
    "previz_critic",
    "render_planner",
]

# The pass the demo runs: ingest, cast, score the cast, check coverage, judge
# the previz, price the render. Every one of these tools is the real thing.
FULL_PASS = [
    Step(agent="script_analyst", tool="load_story_graph", text="Reading the story graph."),
    Step(agent="casting_director", tool="propose_casting"),
    Step(agent="casting_director", tool="judge_casting"),
    Step(agent="shot_designer", tool="check_shot_coverage", args={"scene_ordinal": 1}),
    Step(agent="previz_critic", tool="judge_previz"),
    Step(agent="render_planner", tool="plan_scene_render", args={"scene_ordinal": 1}),
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A configured developer machine must not change what these tests see."""
    for var in (
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_GEMINI_MODEL",
        "STORY_ENGINE_AGENT_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def app(repo: InMemoryRepository):
    application = create_app()
    application.dependency_overrides[get_repo] = lambda: repo
    # No test resolves a real Gemini adapter. `None` is also the honest default
    # for a deployment with the ADK but no LLM, which one test below asserts on.
    application.dependency_overrides[agent_router.get_agent_llm] = lambda: None
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def install_network(app, monkeypatch):
    """Give the app a working ADK: a configured project and a scripted fake SDK."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "story-engine-demo")

    def install(steps, **kwargs):
        api, recorder = build_fake_adk(steps, **kwargs)
        app.dependency_overrides[agent_router.get_agent_runtime] = (
            lambda: AdkAgentRuntime(api_loader=lambda: api)
        )
        return recorder

    return install


def install_missing_sdk(app) -> None:
    """A deployment where ``google-adk`` cannot be imported."""

    def missing():
        raise TerminalProviderError("google-adk is not installed")

    app.dependency_overrides[agent_router.get_agent_runtime] = lambda: AdkAgentRuntime(
        api_loader=missing
    )


def auth_headers(client, email="director@example.com", password="cliff-path-7"):
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def clean_shotlist(scene_ordinal=1):
    """A valid, axis-consistent shot list covering scene 1's dialogue."""
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


def seeded_project(client, headers, sample_fountain, with_shotlist=True) -> str:
    payload = {"title": "The Lighthouse Wager", "rights_attested": True}
    project_id = client.post("/api/v1/projects", json=payload, headers=headers).json()["id"]
    r = client.post(
        f"/api/v1/projects/{project_id}/script",
        files={"file": ("sample.fountain", sample_fountain.encode("utf-8"))},
        headers=headers,
    )
    assert r.status_code == 201
    if with_shotlist:
        r = client.post(
            f"/api/v1/projects/{project_id}/scenes/1/shotlist",
            json=clean_shotlist(1),
            headers=headers,
        )
        assert r.status_code == 201
    return project_id


def parse_sse(body: str) -> list[tuple[str, dict]]:
    """Split an SSE body into (event name, parsed data) pairs."""
    frames = []
    for block in body.strip().split("\n\n"):
        lines = block.split("\n")
        assert lines[0].startswith("event: "), block
        assert lines[1].startswith("data: "), block
        frames.append((lines[0][len("event: ") :], json.loads(lines[1][len("data: ") :])))
    return frames


def of_type(frames, name):
    return [data for event, data in frames if event == name]


# --------------------------------------------------------------------------- #
# GET /agent/network
# --------------------------------------------------------------------------- #
class TestDescribeNetwork:
    def test_serves_the_topology_with_nothing_installed_or_configured(self, client, app):
        """The API boots and answers on a machine with no Google Cloud setup."""
        install_missing_sdk(app)
        r = client.get("/api/v1/agent/network")

        assert r.status_code == 200
        data = r.json()
        assert data["sdk"] == "google-adk"
        assert data["installed"] is False
        assert data["project_configured"] is False
        assert data["available"] is False
        assert "google-adk is not installed" in data["detail"]
        assert [a["name"] for a in data["agents"]] == ["story_director", *SPECIALISTS]

    def test_needs_no_token(self, client):
        """A capability probe, like /health - it describes the deployment."""
        assert client.get("/api/v1/agent/network").status_code == 200

    def test_reports_the_coordinator_and_its_delegates(self, client):
        data = client.get("/api/v1/agent/network").json()
        by_name = {a["name"]: a for a in data["agents"]}

        assert data["coordinator"] == "story_director"
        assert by_name["story_director"]["delegates_to"] == SPECIALISTS
        assert by_name["story_director"]["tools"] == []
        for name in SPECIALISTS:
            assert by_name[name]["delegates_to"] == []
            assert by_name[name]["tools"]
            assert by_name[name]["instruction"]

    def test_every_tool_names_its_owner_and_what_it_does(self, client):
        data = client.get("/api/v1/agent/network").json()
        tools = {t["name"]: t for t in data["tools"]}

        assert tools["judge_previz"]["owner"] == "previz_critic"
        assert tools["plan_scene_render"]["owner"] == "render_planner"
        for tool in tools.values():
            assert tool["owner"] in SPECIALISTS
            assert tool["summary"]

    def test_reports_available_once_the_sdk_and_project_are_there(
        self, client, install_network
    ):
        install_network([])
        data = client.get("/api/v1/agent/network").json()

        assert (data["installed"], data["project_configured"], data["available"]) == (
            True,
            True,
            True,
        )
        assert data["detail"] is None
        assert data["vertex_backend"] is True
        assert data["location"] == "us-central1"

    def test_names_the_project_variable_when_only_that_is_missing(
        self, client, app, monkeypatch
    ):
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        api, _ = build_fake_adk([])
        app.dependency_overrides[agent_router.get_agent_runtime] = (
            lambda: AdkAgentRuntime(api_loader=lambda: api)
        )
        data = client.get("/api/v1/agent/network").json()

        assert data["installed"] is True
        assert data["available"] is False
        assert "GOOGLE_CLOUD_PROJECT" in data["detail"]


# --------------------------------------------------------------------------- #
# POST /projects/{id}/agent/run
# --------------------------------------------------------------------------- #
class TestRun:
    def test_runs_the_full_pipeline_pass(self, client, install_network, sample_fountain):
        recorder = install_network(FULL_PASS)
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        r = client.post(
            f"/api/v1/projects/{project_id}/agent/run", json={}, headers=headers
        )
        assert r.status_code == 200
        data = r.json()

        assert data["status"] == "completed"
        assert data["error"] is None
        assert data["coordinator"] == "story_director"
        assert data["model"] == "gemini-3.8-flash"
        assert data["final_text"].startswith("Production report")
        # The coordinator handed control to four specialists, in stage order.
        assert data["delegations"] == [
            "script_analyst",
            "casting_director",
            "shot_designer",
            "previz_critic",
            "render_planner",
        ]
        # And these are the real pipeline functions that actually executed.
        assert data["tool_calls"] == [
            "load_story_graph",
            "propose_casting",
            "judge_casting",
            "check_shot_coverage",
            "judge_previz",
            "plan_scene_render",
        ]
        # The brief the coordinator was given is the one that reached the SDK.
        assert recorder.prompts == [data["prompt"]]
        assert "The Lighthouse Wager" in data["prompt"]

    def test_the_transcript_carries_real_pipeline_results(
        self, client, install_network, sample_fountain
    ):
        install_network(FULL_PASS)
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        data = client.post(
            f"/api/v1/projects/{project_id}/agent/run", json={}, headers=headers
        ).json()
        results = {
            event["tool"]: event
            for event in data["events"]
            if event["type"] == "tool_result"
        }

        graph = results["load_story_graph"]["result"]
        assert graph["status"] == "ok"
        assert graph["scene_count"] == 4
        assert "MARA" in [c["name"] for c in graph["characters"]]

        casting = results["judge_casting"]["result"]
        assert 0.0 <= casting["overall_score"] <= 1.0

        previz = results["judge_previz"]["result"]
        assert previz["status"] == "ok"
        assert 0.0 <= previz["overall_score"] <= 1.0

        render = results["plan_scene_render"]["result"]
        assert render["shot_count"] == 2
        assert render["estimated_total_cost_cents"] > 0
        assert render["shot_prompts"][0]["prompt"]

    def test_events_are_tagged_and_bookended(self, client, install_network, sample_fountain):
        install_network([Step(agent="previz_critic", tool="judge_previz")])
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        events = client.post(
            f"/api/v1/projects/{project_id}/agent/run", json={}, headers=headers
        ).json()["events"]

        assert events[0]["type"] == "run_started"
        assert events[0]["agents"] == ["story_director", *SPECIALISTS]
        assert events[0]["model"] == "gemini-3.8-flash"
        assert events[-1]["type"] == "run_completed"
        assert events[-1]["status"] == "completed"
        assert [e["type"] for e in events].count("delegation") == 1

    def test_a_custom_brief_is_forwarded_verbatim(
        self, client, install_network, sample_fountain
    ):
        recorder = install_network([])
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        brief = "Only re-score the previz for scene 1."
        data = client.post(
            f"/api/v1/projects/{project_id}/agent/run",
            json={"brief": brief},
            headers=headers,
        ).json()

        assert data["prompt"] == brief
        assert recorder.prompts == [brief]

    def test_the_session_is_keyed_to_the_authenticated_user(
        self, client, install_network, sample_fountain
    ):
        recorder = install_network([])
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        client.post(
            f"/api/v1/projects/{project_id}/agent/run",
            json={"session_id": "demo-take-3"},
            headers=headers,
        )
        session = recorder.sessions[0]
        assert session["app_name"] == "story-engine"
        assert session["session_id"] == "demo-take-3"
        assert session["user_id"]

    @pytest.mark.parametrize("max_scenes", [0, 21])
    def test_rejects_an_out_of_range_scope(
        self, client, install_network, sample_fountain, max_scenes
    ):
        install_network([])
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        r = client.post(
            f"/api/v1/projects/{project_id}/agent/run",
            json={"max_scenes": max_scenes},
            headers=headers,
        )
        assert r.status_code == 422


class TestRunAuthorization:
    def test_401_without_a_token(self, client, install_network, sample_fountain):
        install_network([])
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        assert (
            client.post(f"/api/v1/projects/{project_id}/agent/run", json={}).status_code == 401
        )

    def test_404_for_another_owner(self, client, install_network, sample_fountain):
        install_network([])
        headers_a = auth_headers(client, email="a@example.com")
        headers_b = auth_headers(client, email="b@example.com")
        project_id = seeded_project(client, headers_a, sample_fountain)

        r = client.post(
            f"/api/v1/projects/{project_id}/agent/run", json={}, headers=headers_b
        )
        assert r.status_code == 404


# --------------------------------------------------------------------------- #
# No credentials
# --------------------------------------------------------------------------- #
class TestUnconfigured:
    @pytest.fixture
    def project(self, client, sample_fountain):
        headers = auth_headers(client)
        return seeded_project(client, headers, sample_fountain), headers

    def test_503_naming_the_install_when_the_sdk_is_missing(self, client, app, project):
        project_id, headers = project
        install_missing_sdk(app)

        for path in ("run", "run/stream"):
            r = client.post(
                f"/api/v1/projects/{project_id}/agent/{path}", json={}, headers=headers
            )
            assert r.status_code == 503
            assert "google-adk is not installed" in r.json()["detail"]

    def test_503_naming_the_env_vars_when_no_project_is_configured(
        self, client, app, project, monkeypatch
    ):
        project_id, headers = project
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        api, _ = build_fake_adk([])
        app.dependency_overrides[agent_router.get_agent_runtime] = (
            lambda: AdkAgentRuntime(api_loader=lambda: api)
        )

        for path in ("run", "run/stream"):
            r = client.post(
                f"/api/v1/projects/{project_id}/agent/{path}", json={}, headers=headers
            )
            assert r.status_code == 503
            detail = r.json()["detail"]
            assert "GOOGLE_CLOUD_PROJECT" in detail
            assert "GOOGLE_APPLICATION_CREDENTIALS" in detail

    def test_a_tool_that_needs_gemini_reports_unavailable_and_the_run_carries_on(
        self, client, install_network, sample_fountain
    ):
        """No LLM is a hole in the report, not a failed run."""
        install_network(
            [
                Step(agent="shot_designer", tool="generate_shot_list", args={"scene_ordinal": 1}),
                Step(agent="previz_critic", tool="judge_previz"),
            ]
        )
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        data = client.post(
            f"/api/v1/projects/{project_id}/agent/run", json={}, headers=headers
        ).json()
        results = {e["tool"]: e for e in data["events"] if e["type"] == "tool_result"}

        assert data["status"] == "completed"
        assert results["generate_shot_list"]["status"] == "unavailable"
        assert "GOOGLE_CLOUD_PROJECT" in results["generate_shot_list"]["result"]["reason"]
        assert results["judge_previz"]["status"] == "ok"

    def test_a_project_with_no_script_is_reported_not_raised(
        self, client, install_network, sample_fountain
    ):
        install_network([Step(agent="script_analyst", tool="load_story_graph")])
        headers = auth_headers(client)
        project_id = client.post(
            "/api/v1/projects",
            json={"title": "Empty", "rights_attested": True},
            headers=headers,
        ).json()["id"]

        data = client.post(
            f"/api/v1/projects/{project_id}/agent/run", json={}, headers=headers
        ).json()
        result = next(e for e in data["events"] if e["type"] == "tool_result")

        assert data["status"] == "completed"
        assert result["status"] == "empty"


# --------------------------------------------------------------------------- #
# Failure is surfaced, never swallowed
# --------------------------------------------------------------------------- #
class TestFailureIsSurfaced:
    @pytest.fixture
    def broken_repo(self, client, repo, sample_fountain, monkeypatch):
        """A project that is fine until a specialist's tool touches the script."""
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        def boom(project_id: str):
            raise RuntimeError("story graph store is unreachable")

        monkeypatch.setattr(repo, "get_script", boom)
        return project_id, headers

    def test_the_collected_run_reports_the_failure_and_keeps_the_transcript(
        self, client, install_network, broken_repo
    ):
        install_network(
            [
                Step(agent="script_analyst", tool="load_story_graph"),
                Step(agent="previz_critic", tool="judge_previz"),
            ]
        )
        project_id, headers = broken_repo

        r = client.post(
            f"/api/v1/projects/{project_id}/agent/run", json={}, headers=headers
        )
        assert r.status_code == 200
        data = r.json()

        assert data["status"] == "failed"
        assert "RuntimeError" in data["error"]
        assert "story graph store is unreachable" in data["error"]
        # The tool that broke is named, and the stages after it never ran.
        assert data["tool_calls"] == ["load_story_graph"]

        types = [e["type"] for e in data["events"]]
        assert types[0] == "run_started"
        assert "delegation" in types
        assert "run_failed" in types
        assert types[-1] == "run_completed"

        failure = next(e for e in data["events"] if e["type"] == "run_failed")
        assert failure["error"] == "RuntimeError"
        assert failure["detail"] == "story graph store is unreachable"
        assert data["events"][-1]["status"] == "failed"

    def test_the_stream_ends_with_a_failure_frame_rather_than_going_quiet(
        self, client, install_network, broken_repo
    ):
        install_network([Step(agent="script_analyst", tool="load_story_graph")])
        project_id, headers = broken_repo

        r = client.post(
            f"/api/v1/projects/{project_id}/agent/run/stream", json={}, headers=headers
        )
        assert r.status_code == 200
        frames = parse_sse(r.text)

        assert [name for name, _ in frames][-2:] == ["run_failed", "run_completed"]
        assert of_type(frames, "run_failed")[0]["error"] == "RuntimeError"
        assert of_type(frames, "run_completed")[0]["status"] == "failed"


# --------------------------------------------------------------------------- #
# POST /projects/{id}/agent/run/stream
# --------------------------------------------------------------------------- #
class TestStream:
    @pytest.fixture
    def streamed(self, client, install_network, sample_fountain):
        install_network(FULL_PASS)
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)
        return client.post(
            f"/api/v1/projects/{project_id}/agent/run/stream", json={}, headers=headers
        )

    def test_serves_an_unbuffered_event_stream(self, streamed):
        assert streamed.status_code == 200
        assert streamed.headers["content-type"].startswith("text/event-stream")
        assert streamed.headers["cache-control"] == "no-cache"
        # Without this a proxy would hold every frame back to the end, which is
        # exactly the "one opaque answer" this endpoint exists to avoid.
        assert streamed.headers["x-accel-buffering"] == "no"

    def test_openapi_advertises_only_the_event_stream(self, client):
        """/docs must not promise a JSON body this endpoint never returns."""
        spec = client.get("/openapi.json").json()["paths"][
            "/api/v1/projects/{project_id}/agent/run/stream"
        ]["post"]
        assert list(spec["responses"]["200"]["content"]) == ["text/event-stream"]
        assert "503" in spec["responses"]

    def test_the_stream_opens_with_the_network_and_closes_with_the_summary(self, streamed):
        frames = parse_sse(streamed.text)
        names = [name for name, _ in frames]

        assert names[0] == "run_started"
        assert names[-1] == "run_completed"
        started = frames[0][1]
        assert started["coordinator"] == "story_director"
        assert started["agents"] == ["story_director", *SPECIALISTS]
        assert "judge_previz" in started["tools"]

        completed = frames[-1][1]
        assert completed["status"] == "completed"
        assert completed["delegations"] == SPECIALISTS
        assert completed["event_count"] > 0

    def test_delegations_and_tool_steps_arrive_as_separate_frames(self, streamed):
        frames = parse_sse(streamed.text)

        assert [d["to_agent"] for d in of_type(frames, "delegation")] == SPECIALISTS
        assert all(d["from_agent"] == "story_director" for d in of_type(frames, "delegation"))

        calls = of_type(frames, "tool_call")
        results = of_type(frames, "tool_result")
        assert [c["tool"] for c in calls] == [r["tool"] for r in results]
        assert {c["agent"] for c in calls} == {
            "script_analyst",
            "casting_director",
            "shot_designer",
            "previz_critic",
            "render_planner",
        }
        # The delegation lands before the specialist it hands to says anything.
        names = [name for name, _ in frames]
        assert names.index("delegation") < names.index("tool_call")

    def test_a_specialists_result_is_visible_the_moment_it_lands(self, streamed):
        frames = parse_sse(streamed.text)
        by_tool = {r["tool"]: r for r in of_type(frames, "tool_result")}

        assert by_tool["judge_previz"]["agent"] == "previz_critic"
        assert by_tool["judge_previz"]["status"] == "ok"
        assert by_tool["plan_scene_render"]["result"]["estimated_total_cost_cents"] > 0

    def test_every_frame_is_valid_json_with_the_run_id(self, streamed):
        frames = parse_sse(streamed.text)
        run_ids = {data["run_id"] for _, data in frames}
        assert len(run_ids) == 1
        assert run_ids.pop()

    def test_the_stream_and_the_collected_run_report_the_same_steps(
        self, client, install_network, sample_fountain
    ):
        """Two endpoints, one code path - the transcripts must not drift."""
        install_network(FULL_PASS)
        headers = auth_headers(client)
        project_id = seeded_project(client, headers, sample_fountain)

        streamed = parse_sse(
            client.post(
                f"/api/v1/projects/{project_id}/agent/run/stream", json={}, headers=headers
            ).text
        )
        collected = client.post(
            f"/api/v1/projects/{project_id}/agent/run", json={}, headers=headers
        ).json()["events"]

        assert [name for name, _ in streamed] == [e["type"] for e in collected]
