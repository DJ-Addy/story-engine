"""Runs one set of assertions against every ``Repository`` implementation.

``InMemoryRepository`` and ``SqlAlchemyRepository`` must behave identically to
every router in ``app/api/routers`` - nothing in this codebase is allowed to
depend on which one happens to be wired up by ``app.api.deps.get_repo``. This
module is the thing that pins that: every test function takes the ``repo``
fixture, which is parametrized over both implementations, so a single
assertion failure means the two have drifted.

Substrate: the SQL side runs on an in-memory SQLite database
(``sqlite:///:memory:``), schema created directly from
``app.db.models.STORE_TABLES`` (bypassing Alembic - ``test_db_migration.py``
is what pins the migration to those same models). This is fully offline: no
network, no external process, no real database server, and it is fast enough
to build a fresh schema per test.

What SQLite as a substrate proves: every record round-trips through real SQL
INSERT/SELECT/UPDATE/DELETE statements via SQLAlchemy Core, including JSON
column (de)serialization (``app.db.models._JSON`` is deliberately the plain,
cross-dialect ``JSON`` type for exactly this reason), bytes/BLOB storage, and
the ``_replace``-then-insert overwrite pattern every "one record per key"
table uses.

What it does NOT prove:
  * PostgreSQL's real JSONB storage/indexing behaviour - SQLite's JSON type is
    a TEXT column with app-side (de)serialization, while JSONB on Postgres is
    a real binary, indexable column type. Structural agreement between the
    migration's ``JSONB()`` columns and the model's ``.with_variant(JSONB(),
    "postgresql")`` columns is checked separately (offline, via DDL
    compilation) in ``test_db_migration.py``.
  * Postgres's unique-constraint violation error type/class. This suite never
    exercises a uniqueness violation against SQLAlchemy at all - by design,
    the ``store_*`` schema has none (documented at length in
    ``app/db/models.py``: this record store intentionally mirrors
    ``InMemoryRepository``'s dict-overwrite semantics, not referential/unique
    integrity), so there is no "Postgres raises IntegrityError, SQLite raises
    something else" case to reconcile here.
  * Real transaction/isolation semantics under concurrent access - SQLite's
    locking model differs substantially from PostgreSQL's MVCC, and every
    call in this suite is sequential, single-connection. Nothing here
    exercises two overlapping transactions.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine

from app.api.repo import InMemoryRepository, Repository
from app.continuity.model import Finding
from app.api.repo import CastEntry
from app.db.repository import SqlAlchemyRepository
from app.ingest.elements import NormalizedCharacter, NormalizedScene, StoryGraph
from app.render.audio.model import (
    DEFAULT_RENDER_SETTINGS,
    RenderedClip,
    RenderedSfx,
    SceneRenderSettings,
    SceneTiming,
)
from app.shotlist.schema import SceneShotList, ShotSpec


def _make_in_memory() -> Repository:
    return InMemoryRepository()


def _make_sql() -> Repository:
    engine = create_engine("sqlite:///:memory:", future=True)
    return SqlAlchemyRepository(engine, create_schema=True)


@pytest.fixture(params=["memory", "sql"])
def repo(request: pytest.FixtureRequest) -> Iterator[Repository]:
    factory = _make_in_memory if request.param == "memory" else _make_sql
    r = factory()
    yield r
    engine = getattr(r, "_engine", None)
    if engine is not None:
        engine.dispose()


# --------------------------------------------------------------------------
# Fixture builders for the pydantic payloads the repository stores whole.
# --------------------------------------------------------------------------


def _story_graph() -> StoryGraph:
    return StoryGraph(
        scenes=[
            NormalizedScene(
                ordinal=1,
                slugline="INT. KITCHEN - DAY",
                interior=True,
                location="KITCHEN",
                time_of_day="DAY",
                lines=[],
            )
        ],
        characters=[NormalizedCharacter(canonical_name="BOB", aliases=["ROBERT"])],
    )


def _shotlist(scene_ordinal: int = 1) -> SceneShotList:
    return SceneShotList(
        scene_ordinal=scene_ordinal,
        action_axis="BOB faces the door",
        shots=[
            ShotSpec(
                ordinal=1,
                size="ws",
                subjects=["BOB"],
                axis_side="a",
                lens_mm=35,
                camera_height="eye",
                movement="static",
                eyeline="none",
                covers_lines=[1],
                intent="establish the room",
            )
        ],
    )


def _timing(scene_ordinal: int = 1) -> SceneTiming:
    return SceneTiming(
        scene_ordinal=scene_ordinal,
        duration_ms=1500,
        clips=[
            RenderedClip(
                line_ordinal=1,
                kind="narration",
                character_name=None,
                text="It was a dark and stormy night.",
                start_ms=0,
                duration_ms=1200,
            )
        ],
        sfx=[RenderedSfx(at_ms=200, name="thunder")],
        ambience_tags=["rain"],
    )


def _findings(count: int = 2) -> list[Finding]:
    return [
        Finding(rule_code=f"R{i}", severity="warn", message=f"finding {i}", shot_ordinal=i)
        for i in range(1, count + 1)
    ]


# ==========================================================================
# Users
# ==========================================================================


def test_create_user_round_trips(repo: Repository):
    user = repo.create_user("alice@example.com", "hashed", "salty")
    assert user.email == "alice@example.com"
    assert user.password_hash == "hashed"
    assert user.salt == "salty"
    assert user.id

    fetched = repo.get_user(user.id)
    assert fetched == user


def test_get_user_missing_returns_none(repo: Repository):
    assert repo.get_user("does-not-exist") is None


def test_get_user_by_email_is_case_insensitive(repo: Repository):
    user = repo.create_user("Alice@Example.com", "hashed", "salty")
    assert repo.get_user_by_email("alice@example.com") == user
    assert repo.get_user_by_email("ALICE@EXAMPLE.COM") == user


def test_get_user_by_email_missing_returns_none(repo: Repository):
    assert repo.get_user_by_email("nobody@example.com") is None


def test_duplicate_email_registration_last_one_wins_by_email_lookup(repo: Repository):
    first = repo.create_user("dup@example.com", "hash1", "salt1")
    second = repo.create_user("DUP@example.com", "hash2", "salt2")

    # The email index points at the most recently registered account...
    by_email = repo.get_user_by_email("dup@example.com")
    assert by_email is not None
    assert by_email.id == second.id
    assert by_email.password_hash == "hash2"

    # ...but the first account still exists and is reachable by its own id,
    # unchanged. The repository never merges or deletes it.
    assert repo.get_user(first.id) is not None
    assert repo.get_user(first.id).password_hash == "hash1"


# ==========================================================================
# Projects
# ==========================================================================


def test_create_project_round_trips_with_defaults(repo: Repository):
    user = repo.create_user("owner@example.com", "h", "s")
    project = repo.create_project(user.id, "My Movie", "classical", "strict", True)

    assert project.owner_id == user.id
    assert project.title == "My Movie"
    assert project.grammar_profile == "classical"
    assert project.validator_mode == "strict"
    assert project.rights_attested is True
    assert project.cost_cap_cents == 15000
    assert project.cost_spent_cents == 0

    fetched = repo.get_project(project.id)
    assert fetched == project


def test_get_project_missing_returns_none(repo: Repository):
    assert repo.get_project("does-not-exist") is None


def test_project_field_mutation_persists(repo: Repository):
    """Routers do ``project.cost_spent_cents += n`` on the record the
    repository handed them (there is no ``update_project`` on the protocol).
    Both implementations must make that mutation durable."""
    user = repo.create_user("owner@example.com", "h", "s")
    project = repo.create_project(user.id, "My Movie", "classical", "strict", True)

    project.cost_spent_cents += 500
    assert repo.get_project(project.id).cost_spent_cents == 500

    # Mutating a record fetched via get_project must also persist.
    refetched = repo.get_project(project.id)
    refetched.cost_spent_cents += 250
    assert repo.get_project(project.id).cost_spent_cents == 750

    # ...and so must mutating a record fetched via list_projects.
    listed = repo.list_projects(project.owner_id)[0]
    listed.cost_spent_cents += 100
    assert repo.get_project(project.id).cost_spent_cents == 850


def test_list_projects_filters_by_owner_and_preserves_insertion_order(repo: Repository):
    owner_a = repo.create_user("a@example.com", "h", "s")
    owner_b = repo.create_user("b@example.com", "h", "s")

    a_ids = [
        repo.create_project(owner_a.id, f"A{i}", "classical", "strict", True).id
        for i in range(4)
    ]
    repo.create_project(owner_b.id, "B0", "classical", "strict", True)

    listed = repo.list_projects(owner_a.id)
    assert [p.id for p in listed] == a_ids
    assert all(p.owner_id == owner_a.id for p in listed)


def test_list_projects_empty_for_owner_with_none(repo: Repository):
    owner = repo.create_user("lonely@example.com", "h", "s")
    assert repo.list_projects(owner.id) == []


# ==========================================================================
# Scripts / story graphs
# ==========================================================================


def test_save_and_get_script_round_trips(repo: Repository):
    project = repo.create_project(
        repo.create_user("u@example.com", "h", "s").id, "P", "classical", "strict", True
    )
    graph = _story_graph()
    script = repo.save_script(project.id, "fountain", graph)

    assert script.project_id == project.id
    assert script.format == "fountain"
    assert script.graph == graph

    fetched = repo.get_script(project.id)
    assert fetched == script


def test_get_script_missing_project_returns_none(repo: Repository):
    assert repo.get_script("no-such-project") is None


def test_save_script_twice_keeps_only_the_latest(repo: Repository):
    project = repo.create_project(
        repo.create_user("u@example.com", "h", "s").id, "P", "classical", "strict", True
    )
    first = repo.save_script(project.id, "fountain", _story_graph())
    second = repo.save_script(project.id, "fdx", StoryGraph(scenes=[], characters=[]))

    assert first.id != second.id
    current = repo.get_script(project.id)
    assert current.id == second.id
    assert current.format == "fdx"
    assert current.graph == StoryGraph(scenes=[], characters=[])


def test_update_graph_keeps_the_same_script_id(repo: Repository):
    project = repo.create_project(
        repo.create_user("u@example.com", "h", "s").id, "P", "classical", "strict", True
    )
    original = repo.save_script(project.id, "fountain", _story_graph())
    new_graph = StoryGraph(scenes=[], characters=[])

    updated = repo.update_graph(project.id, new_graph)
    assert updated is not None
    assert updated.id == original.id
    assert updated.graph == new_graph
    assert repo.get_script(project.id).graph == new_graph


def test_update_graph_missing_project_returns_none(repo: Repository):
    assert repo.update_graph("no-such-project", StoryGraph(scenes=[], characters=[])) is None


# ==========================================================================
# Shot lists
# ==========================================================================


def test_save_and_get_shotlist_round_trips(repo: Repository):
    shotlist = _shotlist()
    record = repo.save_shotlist("proj-1", 1, shotlist)

    assert record.project_id == "proj-1"
    assert record.scene_ordinal == 1
    assert record.shotlist == shotlist
    assert repo.get_shotlist("proj-1", 1) == record


def test_get_shotlist_miss_returns_none(repo: Repository):
    assert repo.get_shotlist("proj-1", 99) is None


def test_save_shotlist_twice_overwrites(repo: Repository):
    first = repo.save_shotlist("proj-1", 1, _shotlist(1))
    second = repo.save_shotlist("proj-1", 1, _shotlist(1))

    assert first.id != second.id
    assert repo.get_shotlist("proj-1", 1).id == second.id


def test_shotlists_are_scoped_per_project_and_scene(repo: Repository):
    repo.save_shotlist("proj-1", 1, _shotlist(1))
    repo.save_shotlist("proj-1", 2, _shotlist(2))
    repo.save_shotlist("proj-2", 1, _shotlist(1))

    assert repo.get_shotlist("proj-1", 1).scene_ordinal == 1
    assert repo.get_shotlist("proj-1", 2).scene_ordinal == 2
    assert repo.get_shotlist("proj-2", 1) is not None
    assert repo.get_shotlist("proj-2", 2) is None


# ==========================================================================
# Findings
# ==========================================================================


def test_replace_findings_round_trips_with_defaults(repo: Repository):
    records = repo.replace_findings("proj-1", 1, _findings(2))

    assert len(records) == 2
    for record, expected in zip(records, _findings(2)):
        assert record.project_id == "proj-1"
        assert record.scene_ordinal == 1
        assert record.rule_code == expected.rule_code
        assert record.message == expected.message
        assert record.deliberate is False
        assert record.deliberate_note is None
        assert record.id


def test_list_findings_preserves_insertion_order(repo: Repository):
    repo.replace_findings("proj-1", 1, _findings(3))
    listed = repo.list_findings("proj-1", 1)
    assert [f.rule_code for f in listed] == ["R1", "R2", "R3"]


def test_list_findings_empty_for_untouched_scene(repo: Repository):
    assert repo.list_findings("proj-1", 42) == []


def test_replace_findings_drops_the_previous_batch(repo: Repository):
    first = repo.replace_findings("proj-1", 1, _findings(2))
    repo.replace_findings("proj-1", 1, [Finding(rule_code="R9", severity="error", message="m")])

    listed = repo.list_findings("proj-1", 1)
    assert [f.rule_code for f in listed] == ["R9"]
    # The old findings' ids are gone entirely, not just unlisted.
    assert repo.get_finding(first[0].id) is None


def test_replace_findings_with_empty_list_clears_the_scene(repo: Repository):
    repo.replace_findings("proj-1", 1, _findings(2))
    repo.replace_findings("proj-1", 1, [])
    assert repo.list_findings("proj-1", 1) == []


def test_replace_findings_does_not_touch_other_scenes(repo: Repository):
    repo.replace_findings("proj-1", 1, _findings(1))
    repo.replace_findings("proj-1", 2, _findings(1))
    repo.replace_findings("proj-1", 1, [])

    assert repo.list_findings("proj-1", 1) == []
    assert len(repo.list_findings("proj-1", 2)) == 1


def test_get_finding_missing_returns_none(repo: Repository):
    assert repo.get_finding("no-such-finding") is None


def test_update_finding_persists_deliberate_flag_and_note(repo: Repository):
    records = repo.replace_findings("proj-1", 1, _findings(1))
    finding_id = records[0].id

    updated = repo.update_finding(finding_id, True, "reviewed, intentional")
    assert updated is not None
    assert updated.deliberate is True
    assert updated.deliberate_note == "reviewed, intentional"

    refetched = repo.get_finding(finding_id)
    assert refetched.deliberate is True
    assert refetched.deliberate_note == "reviewed, intentional"


def test_update_finding_missing_returns_none(repo: Repository):
    assert repo.update_finding("no-such-finding", True, "note") is None


# ==========================================================================
# Audio renders
# ==========================================================================


def test_save_and_get_audio_render_round_trips(repo: Repository):
    timing = _timing()
    record = repo.save_audio_render(
        "proj-1", 1, b"RIFF....WAVEDATA", 1500, 1, ["rain"], timing
    )

    assert record.project_id == "proj-1"
    assert record.scene_ordinal == 1
    assert record.wav_bytes == b"RIFF....WAVEDATA"
    assert record.duration_ms == 1500
    assert record.clip_count == 1
    assert record.ambience_tags == ["rain"]
    assert record.timing == timing
    assert record.stale is False
    assert record.stale_reasons == []

    fetched = repo.get_audio_render("proj-1", 1)
    assert fetched == record


def test_get_audio_render_miss_returns_none(repo: Repository):
    assert repo.get_audio_render("proj-1", 99) is None


def test_save_audio_render_twice_overwrites_and_resets_staleness(repo: Repository):
    repo.save_audio_render("proj-1", 1, b"first", 1000, 1, [], _timing())
    repo.mark_audio_render_stale("proj-1", 1, ["edit"])
    assert repo.get_audio_render("proj-1", 1).stale is True

    repo.save_audio_render("proj-1", 1, b"second", 2000, 2, [], _timing())
    fresh = repo.get_audio_render("proj-1", 1)
    assert fresh.wav_bytes == b"second"
    assert fresh.stale is False
    assert fresh.stale_reasons == []


def test_mark_audio_render_stale_accumulates_reasons(repo: Repository):
    repo.save_audio_render("proj-1", 1, b"wav", 1000, 1, [], _timing())
    repo.mark_audio_render_stale("proj-1", 1, ["timeline edit"])
    second = repo.mark_audio_render_stale("proj-1", 1, ["pacing changed"])

    assert second.stale is True
    assert second.stale_reasons == ["timeline edit", "pacing changed"]
    assert repo.get_audio_render("proj-1", 1).stale_reasons == [
        "timeline edit",
        "pacing changed",
    ]


def test_mark_audio_render_stale_missing_render_returns_none(repo: Repository):
    assert repo.mark_audio_render_stale("proj-1", 99, ["x"]) is None


# ==========================================================================
# Per-scene render settings
# ==========================================================================


def test_get_render_settings_defaults_when_unset(repo: Repository):
    settings = repo.get_render_settings("proj-1", 1)
    assert settings == DEFAULT_RENDER_SETTINGS


def test_save_and_get_render_settings_round_trips(repo: Repository):
    custom = SceneRenderSettings(pacing=1.5, ambience_duck=0.2)
    saved = repo.save_render_settings("proj-1", 1, custom)
    assert saved == custom
    assert repo.get_render_settings("proj-1", 1) == custom


def test_save_render_settings_twice_overwrites(repo: Repository):
    repo.save_render_settings("proj-1", 1, SceneRenderSettings(pacing=1.5, ambience_duck=0.2))
    repo.save_render_settings("proj-1", 1, SceneRenderSettings(pacing=0.5, ambience_duck=0.9))
    assert repo.get_render_settings("proj-1", 1) == SceneRenderSettings(
        pacing=0.5, ambience_duck=0.9
    )


def test_render_settings_are_scoped_per_scene(repo: Repository):
    repo.save_render_settings("proj-1", 1, SceneRenderSettings(pacing=2.0, ambience_duck=0.1))
    assert repo.get_render_settings("proj-1", 2) == DEFAULT_RENDER_SETTINGS


# ==========================================================================
# Video renders
# ==========================================================================


def test_save_and_get_video_render_round_trips(repo: Repository):
    record = repo.save_video_render(
        "proj-1", 1, 1, b"MP4BYTES", ["https://example/vid.mp4"], 4000, 250, "google", "veo", "image"
    )

    assert record.project_id == "proj-1"
    assert record.scene_ordinal == 1
    assert record.shot_ordinal == 1
    assert record.video_bytes == b"MP4BYTES"
    assert record.output_urls == ["https://example/vid.mp4"]
    assert record.duration_ms == 4000
    assert record.cost_cents == 250
    assert record.provider == "google"
    assert record.model == "veo"
    assert record.source == "image"

    assert repo.get_video_render("proj-1", 1, 1) == record


def test_get_video_render_miss_returns_none(repo: Repository):
    assert repo.get_video_render("proj-1", 1, 99) is None


def test_save_video_render_twice_overwrites(repo: Repository):
    repo.save_video_render("proj-1", 1, 1, b"first", [], 1000, 10, "google", "veo", "text")
    repo.save_video_render("proj-1", 1, 1, b"second", [], 2000, 20, "google", "veo", "image")

    fresh = repo.get_video_render("proj-1", 1, 1)
    assert fresh.video_bytes == b"second"
    assert fresh.duration_ms == 2000
    assert fresh.source == "image"


def test_video_renders_are_scoped_per_shot(repo: Repository):
    repo.save_video_render("proj-1", 1, 1, b"shot1", [], 1000, 10, "google", "veo", "text")
    repo.save_video_render("proj-1", 1, 2, b"shot2", [], 1000, 10, "google", "veo", "text")

    assert repo.get_video_render("proj-1", 1, 1).video_bytes == b"shot1"
    assert repo.get_video_render("proj-1", 1, 2).video_bytes == b"shot2"


# ==========================================================================
# Shot frames (previz boards)
# ==========================================================================


def test_save_and_get_shot_frame_round_trips(repo: Repository):
    assert repo.save_shot_frame("proj-1", 1, 1, b"PNGBYTES") is None
    assert repo.get_shot_frame("proj-1", 1, 1) == b"PNGBYTES"


def test_get_shot_frame_miss_returns_none(repo: Repository):
    assert repo.get_shot_frame("proj-1", 1, 99) is None


def test_save_shot_frame_twice_overwrites(repo: Repository):
    repo.save_shot_frame("proj-1", 1, 1, b"first")
    repo.save_shot_frame("proj-1", 1, 1, b"second")
    assert repo.get_shot_frame("proj-1", 1, 1) == b"second"


def test_shot_frames_are_scoped_per_shot(repo: Repository):
    repo.save_shot_frame("proj-1", 1, 1, b"a")
    repo.save_shot_frame("proj-1", 1, 2, b"b")
    assert repo.get_shot_frame("proj-1", 1, 1) == b"a"
    assert repo.get_shot_frame("proj-1", 1, 2) == b"b"


# ==========================================================================
# No referential integrity: records may reference ids that were never created
# ==========================================================================


def test_records_may_reference_a_project_id_that_was_never_created(repo: Repository):
    """Documented, deliberate behaviour (see app/db/models.py): the store has
    no foreign keys, matching InMemoryRepository storing anything under any
    key. This is a real conformance requirement, not an oversight - a FK here
    would make the two implementations diverge."""
    ghost_project_id = "no-such-project-ever-existed"

    repo.save_shotlist(ghost_project_id, 1, _shotlist())
    assert repo.get_shotlist(ghost_project_id, 1) is not None

    repo.save_audio_render(ghost_project_id, 1, b"wav", 100, 1, [], _timing())
    assert repo.get_audio_render(ghost_project_id, 1) is not None


# --------------------------------------------------------------------------
# Casting — the voice and tone the renderer reads back
# --------------------------------------------------------------------------


def _cast_entries() -> list[CastEntry]:
    return [
        CastEntry(
            character="ULYSSES",
            voice_id="Iapetus",
            voice_name="Iapetus",
            tone="serious",
            confidence=0.95,
            rationale="Nine lines, commanding his crew.",
        ),
        # The narrator is keyed by None, the same convention the voice map uses.
        CastEntry(character=None, voice_id="Charon", voice_name="Charon", tone=None),
    ]


def test_casting_round_trips(repo: Repository):
    saved = repo.save_casting("proj-1", _cast_entries(), "judge")

    assert saved.id
    assert saved.project_id == "proj-1"
    assert saved.source == "judge"

    fetched = repo.get_casting("proj-1")
    assert fetched == saved


def test_casting_missing_returns_none(repo: Repository):
    assert repo.get_casting("no-such-project") is None


def test_casting_preserves_the_narrator_key_and_a_null_tone(repo: Repository):
    """``None`` must survive the JSON round trip as ``None``, not as "None".

    The narrator is addressed by a null character and an absent tone is a real
    answer meaning "the text gave no signal" — a string would silently become a
    character called "None" with an emotion the adapter would reject.
    """
    repo.save_casting("proj-1", _cast_entries(), "judge")

    fetched = repo.get_casting("proj-1")
    assert fetched is not None
    narrator = fetched.voice_for(None)
    assert narrator is not None
    assert narrator.character is None
    assert narrator.tone is None
    assert narrator.voice_id == "Charon"


def test_casting_floats_survive(repo: Repository):
    repo.save_casting("proj-1", _cast_entries(), "judge")
    fetched = repo.get_casting("proj-1")
    assert fetched is not None
    assert fetched.voice_for("ULYSSES").confidence == 0.95


def test_saving_a_casting_replaces_the_previous_one(repo: Repository):
    """One casting per project, latest wins — not an append-only log."""
    repo.save_casting("proj-1", _cast_entries(), "judge")
    repo.save_casting(
        "proj-1",
        [CastEntry(character="ULYSSES", voice_id="Charon", voice_name="Charon")],
        "manual",
    )

    fetched = repo.get_casting("proj-1")
    assert fetched is not None
    assert len(fetched.entries) == 1
    assert fetched.source == "manual"
    assert fetched.voice_for("ULYSSES").voice_id == "Charon"


def test_castings_are_isolated_by_project(repo: Repository):
    repo.save_casting("proj-1", _cast_entries(), "judge")
    assert repo.get_casting("proj-2") is None
