"""API tests for the ClickHouse analytics spine: the read endpoints and the
write call sites that feed them.

Nothing here reaches a cluster. The stub goes in at the ``QueryRunner`` seam —
the two-method protocol :mod:`app.analytics.mcp_client` defines — so the
recorder, the client, the schema bootstrap and every SQL builder run for real
and only the transport is fake. That is the point of stubbing there rather than
higher up: the interesting assertions are about the statements Story Engine
actually sends, which a stubbed-out client would hide.

Conventions follow tests/test_api_judge.py: a fresh in-memory repo per client,
bearer auth, and a project seeded with the sample screenplay.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app.adapters.fake import FakeVideo
from app.analytics.mcp_client import ClickHouseUnavailable, QueryResult, QueryRunner
from app.analytics.recorder import build_recorder, get_recorder, set_recorder
from app.analytics.settings import AnalyticsSettings
from app.api.deps import get_repo, get_video
from app.api.main import create_app
from app.api.repo import InMemoryRepository
from app.api.routers.analytics import get_analytics_recorder

SOFT = {"id": "soft1", "name": "Bella", "tags": ["female", "soft", "conversational"]}
STRONG = {"id": "strong1", "name": "Domi", "tags": ["female", "expressive", "strong"]}
NARR = {"id": "narr1", "name": "Guy", "tags": ["male", "narrator", "en-US"]}
POOL = [SOFT, STRONG, NARR]

DATABASE = "story_engine"

# What a dashboard SELECT comes back with. The shape only has to satisfy
# QueryResult.dicts(); the queries themselves are unit-tested elsewhere.
PANEL_RESULT = QueryResult(
    columns=["character_name", "voice_name", "avg_score"],
    rows=[["MARA", "Bella", 0.91], ["TOM", "Guy", 0.74]],
)


class FakeRunner:
    """In-memory stand-in for the ``mcp-clickhouse`` transport.

    Records every statement it is handed, which is what lets these tests assert
    that a judge run really did produce an ``INSERT`` and that a panel really
    did issue its ``SELECT``.
    """

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.closed = False
        self.failure: Exception | None = None

    async def run_query(self, sql: str) -> QueryResult:
        self.statements.append(sql)
        if self.failure is not None:
            raise self.failure
        if "system.tables" in sql:
            return QueryResult(
                columns=["name"],
                rows=[["cost_events"], ["judge_scores"], ["render_events"]],
            )
        if sql.lstrip().upper().startswith(("CREATE", "INSERT")):
            return QueryResult()
        return PANEL_RESULT

    async def aclose(self) -> None:
        self.closed = True

    # -- assertion helpers -------------------------------------------------
    def inserts(self, table: str) -> list[str]:
        prefix = f"INSERT INTO {DATABASE}.{table} "
        return [s for s in self.statements if s.startswith(prefix)]

    def selects(self) -> list[str]:
        return [s for s in self.statements if s.lstrip().upper().startswith("SELECT")]


def row_literals(statement: str) -> list[str]:
    """The quoted literals of an ``INSERT``'s VALUES clause, in column order."""
    return re.findall(r"'((?:[^'\\]|\\.)*)'", statement.split(" VALUES ", 1)[1])


def run_ids(statement: str) -> set[str]:
    """Every run_id in a statement. Project ids are dashed uuids, so they miss."""
    return set(re.findall(r"'([0-9a-f]{32})'", statement))


@pytest.fixture
def repo():
    return InMemoryRepository()


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def recorder(runner):
    """A fully real recorder whose only fake part is the transport.

    ``flush_interval_s`` is long so the background drainer never races an
    assertion: every write in these tests is drained by an explicit read, which
    is also what the endpoints do in production.
    """
    settings = AnalyticsSettings(enabled=True, database=DATABASE, flush_interval_s=60.0)
    return build_recorder(settings, runner)


@pytest.fixture
def fake_video():
    return FakeVideo()


@pytest.fixture
def client(repo, recorder, fake_video):
    app = create_app()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[get_video] = lambda: fake_video
    app.dependency_overrides[get_analytics_recorder] = lambda: recorder
    with TestClient(app) as c:
        yield c


def auth_headers(client, email="analyst@example.com", password="tide-mark-9"):
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


def post_shotlist(client, headers, project_id, scene_ordinal=1):
    body = {
        "scene_ordinal": scene_ordinal,
        "action_axis": "MARA to lighthouse door",
        "shots": [
            {
                "ordinal": 1, "size": "ws", "subjects": ["MARA"], "axis_side": "a",
                "lens_mm": 35, "camera_height": "eye", "movement": "static",
                "eyeline": "none", "covers_lines": [1, 3], "intent": "Establish",
            }
        ],
    }
    r = client.post(
        f"/api/v1/projects/{project_id}/scenes/{scene_ordinal}/shotlist",
        json=body,
        headers=headers,
    )
    assert r.status_code == 201


def panel(client, headers, project_id, name="spend"):
    """Read a panel. Reads flush the write buffer first, so this also drains."""
    return client.get(f"/api/v1/projects/{project_id}/analytics/{name}", headers=headers)


def render_video(client, headers, project_id, duration_s=5):
    return client.post(
        f"/api/v1/projects/{project_id}/render/video",
        json={"scene_ordinal": 1, "shot_ordinal": 1, "duration_s": duration_s},
        headers=headers,
    )


# --- the seam -------------------------------------------------------------- #
def test_fake_runner_satisfies_the_query_runner_protocol():
    """If this fails, the double has drifted from the transport it stands in for."""
    assert isinstance(FakeRunner(), QueryRunner)


# --- status ---------------------------------------------------------------- #
class TestStatus:
    def test_reports_configured_reachable_and_migrated(
        self, client, runner, sample_fountain
    ):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)

        r = client.get(f"/api/v1/projects/{project_id}/analytics/status", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True
        assert body["reachable"] is True
        assert body["database"] == DATABASE
        assert body["tables_present"] == ["cost_events", "judge_scores", "render_events"]
        assert set(body["tables_expected"]) == set(body["tables_present"])
        assert body["buffer"]["enabled"] is True
        # Liveness is proved by a real query, not by the presence of credentials.
        assert any("system.tables" in s for s in runner.statements)

    def test_unreachable_cluster_is_reported_not_raised(
        self, client, runner, sample_fountain
    ):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        runner.failure = ClickHouseUnavailable("cluster down")

        r = client.get(f"/api/v1/projects/{project_id}/analytics/status", headers=headers)
        assert r.status_code == 200
        assert r.json()["reachable"] is False
        assert "cluster down" in r.json()["detail"]

    def test_owner_isolation(self, client, sample_fountain):
        headers_a = auth_headers(client, email="a@example.com")
        headers_b = auth_headers(client, email="b@example.com")
        project_id = project_with_script(client, headers_a, sample_fountain)
        r = client.get(
            f"/api/v1/projects/{project_id}/analytics/status", headers=headers_b
        )
        assert r.status_code == 404


# --- judge writes ---------------------------------------------------------- #
class TestJudgeEmitsEvents:
    def test_voice_judgement_is_buffered_then_written_on_read(
        self, client, runner, sample_fountain
    ):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)

        r = client.post(
            f"/api/v1/projects/{project_id}/judge/voices",
            json={"casting": {"MARA": SOFT, "TOM": NARR}, "available_voices": POOL},
            headers=headers,
        )
        assert r.status_code == 200
        # The request path never waits on ClickHouse: nothing has been sent yet.
        assert runner.statements == []

        assert panel(client, headers, project_id, "voice-leaderboard").status_code == 200

        # The schema is bootstrapped before the first write.
        assert any(s.startswith("CREATE DATABASE") for s in runner.statements)
        inserts = runner.inserts("judge_scores")
        assert len(inserts) == 1
        literals = row_literals(inserts[0])  # event_time, run_id, project_id, judge, mode
        assert literals[2] == project_id
        assert literals[3] == "voice_fit"
        assert literals[4] == "single"
        # One overall row plus one per judged character.
        assert "'MARA'" in inserts[0] and "'TOM'" in inserts[0]
        assert len(run_ids(inserts[0])) == 1

    def test_animatic_judgement_carries_the_grammar_profile(
        self, client, repo, runner, sample_fountain
    ):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        post_shotlist(client, headers, project_id)

        r = client.post(f"/api/v1/projects/{project_id}/judge/animatic", headers=headers)
        assert r.status_code == 200
        assert panel(client, headers, project_id, "animatic-trend").status_code == 200

        inserts = runner.inserts("judge_scores")
        assert len(inserts) == 1
        assert "'animatic'" in inserts[0]
        # The profile the scenes were judged under is part of the row: the same
        # shot list scores differently under a different grammar.
        assert f"'{repo.get_project(project_id).grammar_profile}'" in inserts[0]

    def test_ranking_run_shares_one_run_id_across_candidates(
        self, client, runner, sample_fountain
    ):
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
        assert panel(client, headers, project_id, "bake-offs").status_code == 200

        inserts = runner.inserts("judge_scores")
        assert len(inserts) == 1
        assert "'ranking'" in inserts[0]
        assert "'narrator-lead'" in inserts[0] and "'soft-mara'" in inserts[0]
        # The leaderboard reassembles with a GROUP BY run_id, so every
        # candidate's rows must carry the same one.
        assert len(run_ids(inserts[0])) == 1

    def test_two_runs_batch_into_one_statement(self, client, runner, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        for _ in range(2):
            assert (
                client.post(
                    f"/api/v1/projects/{project_id}/judge/voices",
                    json={"casting": {"MARA": SOFT}},
                    headers=headers,
                ).status_code
                == 200
            )
        assert panel(client, headers, project_id, "voice-leaderboard").status_code == 200

        inserts = runner.inserts("judge_scores")
        assert len(inserts) == 1  # batched, not one INSERT per request
        assert len(run_ids(inserts[0])) == 2  # but still two distinct runs


# --- render writes --------------------------------------------------------- #
class TestRendersEmitEvents:
    def test_render_records_the_cost_decision_and_the_render(
        self, client, repo, runner, sample_fountain
    ):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        post_shotlist(client, headers, project_id)

        assert render_video(client, headers, project_id, duration_s=5).status_code == 201
        assert panel(client, headers, project_id, "spend").status_code == 200

        cost = runner.inserts("cost_events")
        render = runner.inserts("render_events")
        assert cost and render

        cost_row = row_literals(cost[0])
        assert cost_row[2] == project_id
        assert cost_row[3] == "render_video"
        assert cost_row[4] == "fake-video"
        # ..., estimated 25, spent_before 0, the default cap, allowed 1, headroom.
        cap = repo.get_project(project_id).cost_cap_cents
        assert cost[0].rstrip().endswith(f", 25, 0, {cap}, 1, {cap - 25})")

        render_row = row_literals(render[0])
        assert render_row[2] == project_id
        assert render_row[3] == "video"
        assert render_row[4] == "fake-video"
        assert render_row[6] == "text"  # no board frame stored -> text-to-video
        assert render_row[7] == "ok"
        # Actual and estimated cost are both kept, so the estimator is auditable.
        assert ", 25, 25, " in render[0]
        # One run_id joins the governor's decision to the render it authorised.
        assert run_ids(cost[0]) == run_ids(render[0])

    def test_cost_cap_refusal_is_recorded_with_negative_headroom(
        self, client, repo, runner, sample_fountain
    ):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        post_shotlist(client, headers, project_id)
        repo.get_project(project_id).cost_cap_cents = 1

        assert render_video(client, headers, project_id, duration_s=5).status_code == 402
        assert panel(client, headers, project_id, "cost-pressure").status_code == 200

        cost = runner.inserts("cost_events")
        assert len(cost) == 1
        # allowed = 0, headroom = cap 1 - (spent 0 + estimated 25).
        assert cost[0].rstrip().endswith(", 25, 0, 1, 0, -24)")
        # A refusal renders nothing, which is exactly why the decision is an event.
        assert runner.inserts("render_events") == []


# --- reads ----------------------------------------------------------------- #
class TestPanels:
    def test_panel_returns_clickhouse_rows(self, client, runner, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)

        r = panel(client, headers, project_id, "voice-leaderboard")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert body["columns"] == PANEL_RESULT.columns
        assert body["rows"][0] == {
            "character_name": "MARA",
            "voice_name": "Bella",
            "avg_score": 0.91,
        }
        # The project is scoped in SQL, not filtered in Python.
        assert f"project_id = '{project_id}'" in runner.selects()[-1]

    def test_dashboard_returns_every_panel_in_one_request(self, client, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)

        r = panel(client, headers, project_id, "dashboard")
        assert r.status_code == 200
        body = r.json()
        assert body["available"] is True
        assert len(body["panels"]) == 6
        assert all(p["available"] for p in body["panels"])

    def test_unreachable_cluster_degrades_at_200(self, client, runner, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        runner.failure = ClickHouseUnavailable("cluster down")

        r = panel(client, headers, project_id, "voice-leaderboard")
        assert r.status_code == 200
        assert r.json()["available"] is False
        assert "cluster down" in r.json()["detail"]
        assert r.json()["rows"] == []

        dash = panel(client, headers, project_id, "dashboard")
        assert dash.status_code == 200
        assert dash.json()["available"] is False

    def test_a_judge_run_survives_an_unreachable_cluster(
        self, client, runner, sample_fountain
    ):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        runner.failure = ClickHouseUnavailable("cluster down")

        r = client.post(
            f"/api/v1/projects/{project_id}/judge/voices",
            json={"casting": {"MARA": SOFT}},
            headers=headers,
        )
        assert r.status_code == 200
        assert panel(client, headers, project_id, "voice-leaderboard").status_code == 200


# --- telemetry is never worth the request ---------------------------------- #
class TestRecordFailuresAreNeverFatal:
    class ExplodingRecorder:
        """A recorder that breaks its own contract, to prove nothing leans on it."""

        client = None

        def __init__(self) -> None:
            self.calls = 0

        @property
        def enabled(self) -> bool:
            return True

        def record(self, events) -> int:
            self.calls += 1
            raise RuntimeError("buffer exploded")

        def stats(self) -> dict:
            return {"enabled": True, "buffered": 0, "written": 0, "dropped": 0, "failed": 0}

        async def flush(self) -> int:
            raise RuntimeError("flush exploded")

    @pytest.fixture
    def exploding(self):
        return self.ExplodingRecorder()

    @pytest.fixture
    def client(self, repo, fake_video, exploding):
        app = create_app()
        app.dependency_overrides[get_repo] = lambda: repo
        app.dependency_overrides[get_video] = lambda: fake_video
        app.dependency_overrides[get_analytics_recorder] = lambda: exploding
        with TestClient(app) as c:
            yield c

    def test_judge_still_returns_its_judgement(self, client, exploding, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        r = client.post(
            f"/api/v1/projects/{project_id}/judge/voices",
            json={"casting": {"MARA": SOFT}, "available_voices": POOL},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["characters"]
        assert exploding.calls == 1

    def test_render_still_returns_its_clip(self, client, exploding, repo, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        post_shotlist(client, headers, project_id)

        r = render_video(client, headers, project_id)
        assert r.status_code == 201
        assert r.json()["has_video"] is True
        # Both the cost decision and the finished render tried to record.
        assert exploding.calls == 2
        assert repo.get_project(project_id).cost_spent_cents == 25

    def test_cost_cap_refusal_is_still_a_402(self, client, repo, sample_fountain):
        headers = auth_headers(client)
        project_id = project_with_script(client, headers, sample_fountain)
        post_shotlist(client, headers, project_id)
        repo.get_project(project_id).cost_cap_cents = 1
        assert render_video(client, headers, project_id).status_code == 402


# --- the app must serve identically with no cluster ------------------------ #
class TestAnalyticsDisabled:
    def test_app_starts_and_serves_without_clickhouse(
        self, monkeypatch, repo, sample_fountain
    ):
        """No CLICKHOUSE_HOST: everything still works and analytics is a no-op."""
        monkeypatch.delenv("CLICKHOUSE_HOST", raising=False)
        app = create_app()
        app.dependency_overrides[get_repo] = lambda: repo

        with TestClient(app) as c:
            assert c.get("/api/v1/health").json() == {"status": "ok"}
            # The lifespan installed a recorder; it is simply disabled.
            live = get_recorder()
            assert live.enabled is False
            assert live.client is None

            headers = auth_headers(c, email="offline@example.com")
            project_id = project_with_script(c, headers, sample_fountain)
            r = c.post(
                f"/api/v1/projects/{project_id}/judge/voices",
                json={"casting": {"MARA": SOFT}, "available_voices": POOL},
                headers=headers,
            )
            assert r.status_code == 200
            assert live.stats()["buffered"] == 0  # dropped on the floor, silently

            status = c.get(
                f"/api/v1/projects/{project_id}/analytics/status", headers=headers
            ).json()
            assert status["configured"] is False
            assert status["reachable"] is False
            assert "CLICKHOUSE_HOST" in status["detail"]
            assert status["tables_present"] == []

            dash = c.get(
                f"/api/v1/projects/{project_id}/analytics/dashboard", headers=headers
            ).json()
            assert dash["available"] is False
            assert dash["panels"] == []

    def test_lifespan_builds_from_the_environment_and_tears_down(self, monkeypatch):
        """A configured host yields an enabled recorder, cleared again on shutdown.

        Construction is offline by design — the MCP server is not launched until
        the first statement — so this covers the startup/shutdown wiring without
        a cluster, a subprocess or a socket.
        """
        monkeypatch.setenv("CLICKHOUSE_HOST", "cluster.invalid")
        monkeypatch.setenv("STORY_ENGINE_ANALYTICS_ENABLED", "1")
        try:
            app = create_app()
            with TestClient(app) as c:
                assert c.get("/api/v1/health").status_code == 200
                live = get_recorder()
                assert live.enabled is True
                assert live.client is not None
                assert live.client.database == "story_engine"
            # shutdown_recorder cleared the process-wide instance, so asking
            # again cannot hand back the closed one.
            assert get_recorder() is not live
        finally:
            set_recorder(None)
