"""``SqlAlchemyRepository``-specific behaviour not shared with the in-memory side.

``test_repo_conformance.py`` pins the two implementations to identical
observable behaviour through the ``Repository`` protocol. This module covers
things that are true only because this implementation is backed by real SQL:
schema creation being opt-in, bytes coming back as real ``bytes`` (not a
driver-specific buffer type), and JSON round-tripping nested pydantic payloads
with every optional field populated. Substrate is SQLite in-memory, same as
the conformance suite - see that module's docstring for what that does and
does not prove about PostgreSQL.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy import create_engine

from app.db import models
from app.db.repository import SqlAlchemyRepository
from app.ingest.elements import AttributedLine, NormalizedCharacter, NormalizedScene, StoryGraph
from app.shotlist.schema import SceneShotList, ShotSpec


def _repo(*, create_schema: bool = True) -> tuple[SqlAlchemyRepository, sa.engine.Engine]:
    engine = create_engine("sqlite:///:memory:", future=True)
    return SqlAlchemyRepository(engine, create_schema=create_schema), engine


def test_create_schema_false_leaves_tables_absent():
    _repo_instance, engine = _repo(create_schema=False)
    try:
        inspector = sa.inspect(engine)
        assert inspector.get_table_names() == []
    finally:
        engine.dispose()


def test_create_schema_true_creates_the_store_tables():
    _repo_instance, engine = _repo(create_schema=True)
    try:
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        assert {t.name for t in models.STORE_TABLES} <= table_names
    finally:
        engine.dispose()


def test_wav_bytes_come_back_as_real_bytes():
    repo, engine = _repo()
    try:
        from app.render.audio.model import SceneTiming

        repo.save_audio_render(
            "p1", 1, b"\x00\x01wav", 100, 1, [], SceneTiming(scene_ordinal=1, duration_ms=100)
        )
        fetched = repo.get_audio_render("p1", 1)
        assert type(fetched.wav_bytes) is bytes
    finally:
        engine.dispose()


def test_video_bytes_come_back_as_real_bytes():
    repo, engine = _repo()
    try:
        repo.save_video_render("p1", 1, 1, b"\x00mp4", [], 100, 10, "google", "veo", "text")
        fetched = repo.get_video_render("p1", 1, 1)
        assert type(fetched.video_bytes) is bytes
    finally:
        engine.dispose()


def test_shot_frame_bytes_come_back_as_real_bytes():
    repo, engine = _repo()
    try:
        repo.save_shot_frame("p1", 1, 1, b"\x00png")
        fetched = repo.get_shot_frame("p1", 1, 1)
        assert type(fetched) is bytes
    finally:
        engine.dispose()


def test_story_graph_json_round_trip_preserves_every_optional_field():
    repo, engine = _repo()
    try:
        graph = StoryGraph(
            scenes=[
                NormalizedScene(
                    ordinal=1,
                    slugline="INT. KITCHEN - DAY",
                    interior=True,
                    location="KITCHEN",
                    time_of_day="DAY",
                    lines=[
                        AttributedLine(
                            ordinal=1,
                            kind="dialogue",
                            text="Hello.",
                            character_name="BOB",
                            emotion="cheerful",
                            attribution_confidence=0.87,
                            attribution_source="cue",
                        ),
                        AttributedLine(
                            ordinal=2,
                            kind="narration",
                            text="He left.",
                            character_name=None,
                            emotion=None,
                            attribution_confidence=None,
                            attribution_source=None,
                        ),
                    ],
                ),
                NormalizedScene(
                    ordinal=2, slugline=None, interior=None, location=None, time_of_day=None
                ),
            ],
            characters=[
                NormalizedCharacter(canonical_name="BOB", aliases=["ROBERT", "ROB"], line_count=1)
            ],
        )
        repo.save_script("p1", "fountain", graph)
        fetched = repo.get_script("p1").graph
        assert fetched == graph
        assert fetched.scenes[0].lines[0].attribution_confidence == 0.87
        assert fetched.scenes[1].slugline is None
    finally:
        engine.dispose()


def test_shotlist_json_round_trip_preserves_all_shot_fields():
    repo, engine = _repo()
    try:
        shotlist = SceneShotList(
            scene_ordinal=1,
            action_axis="axis",
            shots=[
                ShotSpec(
                    ordinal=1,
                    size="ecu",
                    subjects=["BOB", "ALICE"],
                    axis_side="b",
                    lens_mm=85,
                    camera_height="low",
                    movement="dolly",
                    eyeline="to_camera",
                    covers_lines=[1, 2, 3],
                    intent="close on Bob's reaction",
                ),
                ShotSpec(
                    ordinal=2,
                    size="ws",
                    subjects=[],
                    axis_side="neutral",
                    lens_mm=24,
                    camera_height="overhead",
                    movement="crane",
                    eyeline="none",
                    covers_lines=[],
                    intent="establish",
                ),
            ],
        )
        repo.save_shotlist("p1", 1, shotlist)
        fetched = repo.get_shotlist("p1", 1).shotlist
        assert fetched == shotlist
        assert fetched.shots[0].subjects == ["BOB", "ALICE"]
    finally:
        engine.dispose()


def test_two_repositories_on_separate_engines_do_not_share_state():
    repo_a, engine_a = _repo()
    repo_b, engine_b = _repo()
    try:
        repo_a.create_user("a@example.com", "h", "s")
        assert repo_b.get_user_by_email("a@example.com") is None
    finally:
        engine_a.dispose()
        engine_b.dispose()


def test_repository_exposes_its_engine_for_disposal():
    repo, engine = _repo()
    try:
        assert repo._engine is engine
    finally:
        engine.dispose()
