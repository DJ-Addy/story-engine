"""API tests: the env-gated demo project seeder.

Fixture expectations come from ``app/demo/lighthouse.fountain``, which is the
ingest fixture copied into the package (see ``app/demo/__init__.py`` for why it
cannot live in ``tests/``): 'FADE IN:' before the first slugline gives a
preamble scene 0 plus three slugline scenes, and the cues MARA / TOM /
TOM (CONT'D) / MARA (V.O.) canonicalize to MARA and TOM.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from importlib import resources

import pytest
from fastapi.testclient import TestClient

from app.api import auth, deps
from app.api.deps import get_repo
from app.api.main import create_app
from app.api.repo import InMemoryRepository
from app.demo import seed
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize

DISABLED_SHAPE = {
    "enabled": False,
    "seeded": False,
    "project_id": None,
    "title": None,
    "scene_count": 0,
}


def _client(repo: InMemoryRepository) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_repo] = lambda: repo
    return TestClient(app)


@pytest.fixture
def repo() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def demo_on(monkeypatch):
    """Neutralize whatever the machine (or backend/.env) says about the switch."""
    monkeypatch.delenv("STORY_ENGINE_DEMO", raising=False)


@pytest.fixture
def spare_startup_repo(monkeypatch) -> InMemoryRepository:
    """Keep the lifespan's seed out of the process-wide repository singleton.

    ``lifespan`` seeds ``deps.get_repo()``, which is a module global shared with
    every other test module in the session — so left alone these tests would
    both depend on and pollute it. Pointing it at a throwaway also keeps the
    repository the routes see (installed via ``dependency_overrides``) unseeded,
    which is what makes "before anyone has asked for a session" testable at all.
    """
    spare = InMemoryRepository()
    monkeypatch.setattr(deps, "_repo", spare)
    return spare


@pytest.fixture
def client(repo, demo_on, spare_startup_repo):
    with _client(repo) as c:
        yield c


def start_session(client) -> tuple[str, dict[str, str]]:
    r = client.post("/api/v1/demo/session")
    assert r.status_code == 200
    body = r.json()
    return body["project_id"], {"Authorization": f"Bearer {body['access_token']}"}


# --------------------------------------------------------------------------
# Gating
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF", "  False  "])
def test_disabled_env_values_turn_the_demo_off(value, repo, spare_startup_repo, monkeypatch):
    monkeypatch.setenv("STORY_ENGINE_DEMO", value)
    with _client(repo) as client:
        assert client.get("/api/v1/demo").json() == DISABLED_SHAPE

        r = client.post("/api/v1/demo/session")
        assert r.status_code == 403
        assert "STORY_ENGINE_DEMO" in r.json()["detail"]

    # Neither the request nor the lifespan may have written anything.
    assert seed.find_demo(repo) is None
    assert seed.find_demo(spare_startup_repo) is None


@pytest.mark.parametrize("value", ["", "1", "true", "yes", "on", "sure"])
def test_demo_is_on_unless_explicitly_switched_off(value, monkeypatch):
    monkeypatch.setenv("STORY_ENGINE_DEMO", value)
    assert seed.demo_enabled() is True


def test_demo_is_on_when_the_variable_is_absent(monkeypatch):
    monkeypatch.delenv("STORY_ENGINE_DEMO", raising=False)
    assert seed.demo_enabled() is True


# --------------------------------------------------------------------------
# GET /demo
# --------------------------------------------------------------------------


def test_status_before_seeding_is_enabled_but_empty(client):
    assert client.get("/api/v1/demo").json() == {
        "enabled": True,
        "seeded": False,
        "project_id": None,
        "title": None,
        "scene_count": 0,
    }


def test_status_after_seeding_names_the_project(client):
    project_id, _ = start_session(client)
    assert client.get("/api/v1/demo").json() == {
        "enabled": True,
        "seeded": True,
        "project_id": project_id,
        "title": seed.DEMO_TITLE,
        "scene_count": 1,
    }


def test_status_never_seeds(client, repo):
    for _ in range(3):
        assert client.get("/api/v1/demo").json()["seeded"] is False
    assert repo.get_user_by_email(seed.DEMO_EMAIL) is None


def test_status_answers_even_when_the_repository_is_broken(demo_on, spare_startup_repo):
    """The landing page's first call must never be the thing that 500s."""

    class BrokenRepo(InMemoryRepository):
        def get_user_by_email(self, email):
            raise RuntimeError("repository is down")

    with _client(BrokenRepo()) as client:
        r = client.get("/api/v1/demo")
    assert r.status_code == 200
    assert r.json() == {**DISABLED_SHAPE, "enabled": True}


# --------------------------------------------------------------------------
# POST /demo/session
# --------------------------------------------------------------------------


def test_session_returns_a_bearer_token_that_opens_the_project(client):
    r = client.post("/api/v1/demo/session")
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    project = client.get(f"/api/v1/projects/{body['project_id']}", headers=headers)
    assert project.status_code == 200
    assert project.json()["title"] == seed.DEMO_TITLE
    assert project.json()["rights_attested"] is True


def test_session_token_is_required_to_reach_the_project(client):
    project_id, _ = start_session(client)
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 401


def test_repeated_sessions_reuse_one_user_and_one_project(client, repo):
    first = client.post("/api/v1/demo/session").json()
    second = client.post("/api/v1/demo/session").json()
    third = client.post("/api/v1/demo/session").json()

    assert first["project_id"] == second["project_id"] == third["project_id"]
    subjects = {auth.decode_token(r["access_token"]) for r in (first, second, third)}
    assert len(subjects) == 1

    user = repo.get_user_by_email(seed.DEMO_EMAIL)
    assert user is not None
    assert [p.id for p in repo.list_projects(user.id)] == [first["project_id"]]


def test_demo_user_owns_only_the_demo_project(client):
    project_id, headers = start_session(client)
    listed = client.get("/api/v1/projects", headers=headers).json()
    assert [p["id"] for p in listed] == [project_id]


def test_demo_user_cannot_be_logged_into(client):
    """The session endpoint is the only door; the password is not a shared secret."""
    start_session(client)
    for guess in ("demo1234", "password", "story-engine", seed.DEMO_EMAIL):
        r = client.post(
            "/api/v1/auth/login", json={"email": seed.DEMO_EMAIL, "password": guess}
        )
        assert r.status_code == 401


def test_a_visitors_own_project_does_not_displace_the_seeded_one(client, repo):
    """A demo session can create projects; the seed must keep pointing at its own."""
    project_id, headers = start_session(client)
    client.post(
        "/api/v1/projects",
        json={"title": "A visitor's experiment", "rights_attested": True},
        headers=headers,
    )
    assert client.get("/api/v1/demo").json()["project_id"] == project_id
    assert client.post("/api/v1/demo/session").json()["project_id"] == project_id


def test_ensure_seeded_is_safe_under_concurrent_callers(repo):
    """Two cold-start requests arriving together must not seed twice.

    Driven against the seeder rather than through the client so the threads
    really do collide inside ``ensure_seeded``, which is where the guard is.
    """
    workers = 8
    ready = threading.Barrier(workers, timeout=10)

    def seed_once():
        ready.wait()
        return seed.ensure_seeded(repo)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = [f.result() for f in [pool.submit(seed_once) for _ in range(workers)]]

    assert len({d.project.id for d in results}) == 1
    assert len({d.user.id for d in results}) == 1
    user = repo.get_user_by_email(seed.DEMO_EMAIL)
    assert len(repo.list_projects(user.id)) == 1


def test_a_partial_seed_is_completed_rather_than_duplicated(repo):
    """A user with no project (an interrupted seed) resumes, it does not re-create."""
    user = repo.create_user(seed.DEMO_EMAIL, "not-a-real-hash", auth.new_salt())
    demo = seed.ensure_seeded(repo)
    assert demo.user.id == user.id
    assert demo.scene_count == 1
    assert len(repo.list_projects(user.id)) == 1


# --------------------------------------------------------------------------
# The seeded content
# --------------------------------------------------------------------------


def test_screenplay_ships_inside_the_installed_package():
    """Guards the pyproject package-data entry: tests/ never reaches the image."""
    packaged = resources.files("app.demo").joinpath("odyssey_sirens.fountain")
    assert packaged.is_file()
    assert seed.screenplay_text().startswith("Title: The Odyssey - Book XII, The Sirens")


def test_seeded_graph_is_what_the_packaged_fountain_parses_to(client):
    project_id, headers = start_session(client)
    graph = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers).json()

    expected = normalize(parse_fountain(seed.screenplay_text()))
    assert graph == expected.model_dump(mode="json")


def test_seeded_graph_has_the_scenes_and_characters_of_the_screenplay(client):
    """The sample is Book XII of the Odyssey, converted from prose.

    What this pins is that the packaged Fountain round-trips through the same
    parser POST /script uses: one real scene, two speaking parts, and every
    dialogue line attributed by cue at full confidence - which is exactly what
    a converted novel must look like once it re-enters the screenplay path.
    """
    project_id, headers = start_session(client)
    graph = client.get(f"/api/v1/projects/{project_id}/graph", headers=headers).json()

    real_scenes = [s for s in graph["scenes"] if s["ordinal"] >= 1]
    assert [s["ordinal"] for s in real_scenes] == [1]
    assert {c["canonical_name"] for c in graph["characters"]} == {"ULYSSES", "THE SIRENS"}

    dialogue = [
        line
        for scene in graph["scenes"]
        for line in scene["lines"]
        if line["kind"] == "dialogue"
    ]
    assert dialogue, "the passage has speech in it"
    assert {line["character_name"] for line in dialogue} == {"ULYSSES", "THE SIRENS"}
    assert all(line["attribution_source"] == "cue" for line in dialogue)
    assert all(line["attribution_confidence"] == 1.0 for line in dialogue)

def test_the_seeded_scene_has_a_timeline_to_look_at(client):
    """What a reviewer actually opens: a scene view, with no render and no provider."""
    project_id, headers = start_session(client)
    r = client.get(f"/api/v1/projects/{project_id}/scenes/1/timeline", headers=headers)
    assert r.status_code == 200
    assert r.json()["timing_source"] == "estimated"
    assert r.json()["dialogue"]


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------


def test_lifespan_seeds_before_the_first_request(repo, demo_on, monkeypatch):
    """A cold Cloud Run instance is ready without anyone POSTing first."""
    monkeypatch.setattr(deps, "_repo", repo)
    with _client(repo) as client:
        status = client.get("/api/v1/demo").json()
    assert status["seeded"] is True
    assert status["scene_count"] == 1


def test_lifespan_does_not_seed_when_the_demo_is_off(repo, monkeypatch):
    monkeypatch.setenv("STORY_ENGINE_DEMO", "0")
    monkeypatch.setattr(deps, "_repo", repo)
    with _client(repo):
        pass
    assert seed.find_demo(repo) is None


def test_a_failing_seed_does_not_stop_the_api_booting(repo, demo_on, monkeypatch):
    class BrokenRepo(InMemoryRepository):
        def get_user_by_email(self, email):
            raise RuntimeError("repository is down")

    monkeypatch.setattr(deps, "_repo", BrokenRepo())
    with _client(repo) as client:
        assert client.get("/api/v1/health").status_code == 200
