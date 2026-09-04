"""Story Engine persistence models (PRD §3.2), targeting PostgreSQL 16.

SQLAlchemy 2.0 declarative style (Mapped / mapped_column). All primary keys
are UUIDs generated server-side via gen_random_uuid(); timestamps are
timestamptz. Schema creation is handled by Alembic (see
backend/alembic/versions/0001_initial_schema.py), which also creates the
btree_gist extension required by the character_variants exclusion constraint.

Two schemas live here:

* the normalized PRD tables (``users`` ... ``jobs``, migration 0001), the
  target model for the ingest pipeline, not yet written to by anything;
* the ``store_*`` record store (migration 0002), which is what
  ``app.db.repository.SqlAlchemyRepository`` actually reads and writes so a
  Cloud Run restart stops erasing every project. See the section header
  further down for why it is separate and deliberately dialect-portable.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import (
    ARRAY,
    ENUM,
    INT4RANGE,
    JSONB,
    REAL,
    TIMESTAMP,
    UUID,
    ExcludeConstraint,
    Range,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# --------------------------------------------------------------------------
# Native PostgreSQL enum types
# --------------------------------------------------------------------------

source_format_enum = ENUM(
    "fountain", "fdx", "pdf_screenplay", "epub", "txt",
    name="source_format",
)
line_kind_enum = ENUM(
    "narration", "dialogue", "action", "parenthetical", "transition",
    name="line_kind",
)
shot_size_enum = ENUM(
    "ecu", "cu", "mcu", "ms", "mws", "ws", "ews", "insert", "pov",
    name="shot_size",
)
axis_side_enum = ENUM(
    "a", "b", "neutral", "crossing",
    name="axis_side",
)
job_state_enum = ENUM(
    "queued", "running", "succeeded", "failed", "cancelled",
    name="job_state",
)

# --------------------------------------------------------------------------
# Reusable column fragments
# --------------------------------------------------------------------------

def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = _created_at()


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    grammar_profile: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'classical'")
    )
    validator_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'strict'")
    )
    rights_attested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    cost_cap_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("15000")
    )
    cost_spent_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = _created_at()


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    format: Mapped[str] = mapped_column(source_format_enum, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str] = mapped_column(Text, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (
        UniqueConstraint("project_id", "canonical_name"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    canonical_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_range: Mapped[Range[int] | None] = mapped_column(INT4RANGE, nullable=True)
    line_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_narrator: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class ReferenceSet(Base):
    __tablename__ = "reference_sets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    seed: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    gen_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


class ReferenceImage(Base):
    __tablename__ = "reference_images"

    id: Mapped[uuid.UUID] = _uuid_pk()
    reference_set_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reference_sets.id", ondelete="CASCADE"),
        nullable=False,
    )
    angle: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class CharacterVariant(Base):
    """A character look valid over a scene range.

    The exclusion constraint guarantees two variants of the same character
    can never cover overlapping scene ranges (an open-ended scene_to is
    treated as extending to INT4 max). Requires the btree_gist extension.
    """

    __tablename__ = "character_variants"
    __table_args__ = (
        ExcludeConstraint(
            (text("character_id"), "="),
            (text("int4range(scene_from, COALESCE(scene_to, 2147483647))"), "&&"),
            using="gist",
            name="excl_character_variants_scene_range",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    character_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("characters.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[str] = mapped_column(Text, nullable=False)
    scene_from: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_to: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wardrobe: Mapped[str | None] = mapped_column(Text, nullable=True)
    condition: Mapped[str | None] = mapped_column(Text, nullable=True)
    props: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    reference_set_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reference_sets.id"), nullable=True
    )


class Scene(Base):
    __tablename__ = "scenes"
    __table_args__ = (
        UniqueConstraint("script_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    script_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scripts.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    slugline: Mapped[str | None] = mapped_column(Text, nullable=True)
    interior: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_of_day: Mapped[str | None] = mapped_column(Text, nullable=True)
    weather: Mapped[str | None] = mapped_column(Text, nullable=True)
    mood: Mapped[str | None] = mapped_column(Text, nullable=True)
    ambience_tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    page_start: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    page_end: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)


class Line(Base):
    __tablename__ = "lines"
    __table_args__ = (
        UniqueConstraint("scene_id", "ordinal"),
        Index("ix_lines_scene_id_ordinal", "scene_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(line_kind_enum, nullable=False)
    character_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("characters.id"), nullable=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    emotion: Mapped[str | None] = mapped_column(Text, nullable=True)
    attribution_confidence: Mapped[float | None] = mapped_column(REAL, nullable=True)
    attribution_source: Mapped[str | None] = mapped_column(Text, nullable=True)


class Shot(Base):
    __tablename__ = "shots"
    __table_args__ = (
        UniqueConstraint("scene_id", "ordinal"),
        Index("ix_shots_scene_id_ordinal", "scene_id", "ordinal"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    size: Mapped[str] = mapped_column(shot_size_enum, nullable=False)
    subject_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default=text("'{}'::uuid[]")
    )
    axis_side: Mapped[str] = mapped_column(
        axis_side_enum, nullable=False, server_default=text("'neutral'")
    )
    lens_mm: Mapped[float | None] = mapped_column(REAL, nullable=True)
    camera_height: Mapped[str | None] = mapped_column(Text, nullable=True)
    movement: Mapped[str | None] = mapped_column(Text, nullable=True)
    eyeline: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_id_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lines.id"), nullable=True
    )
    line_id_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lines.id"), nullable=True
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_text: Mapped[str | None] = mapped_column(Text, nullable=True)


class ContinuityFinding(Base):
    __tablename__ = "continuity_findings"
    __table_args__ = (
        Index(
            "ix_continuity_findings_scene_id_active",
            "scene_id",
            postgresql_where=text("deliberate = false"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    scene_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=False
    )
    shot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("shots.id", ondelete="CASCADE"), nullable=True
    )
    rule_code: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    deliberate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    deliberate_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AudioClip(Base):
    __tablename__ = "audio_clips"

    id: Mapped[uuid.UUID] = _uuid_pk()
    line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("lines.id", ondelete="CASCADE"), nullable=True
    )
    scene_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenes.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    lufs: Mapped[float | None] = mapped_column(REAL, nullable=True)
    provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(Text, nullable=True)
    gen_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_project_id_state", "project_id", "state"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        job_state_enum, nullable=False, server_default=text("'queued'")
    )
    idempotency_key: Mapped[str | None] = mapped_column(Text, nullable=True, unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    progress: Mapped[float] = mapped_column(
        REAL, nullable=False, server_default=text("0")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = _created_at()
    finished_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )


# --------------------------------------------------------------------------
# API record store
# --------------------------------------------------------------------------
#
# Everything above models the fully normalized ingest target from PRD 3.2.
# Nothing writes to it yet: the running API stores whole pydantic records
# through ``app.api.repo.Repository`` (a story graph is one blob, not a scene /
# line / shot tree), so migration 0001 alone would still lose every project on
# a Cloud Run restart.
#
# The ``store_*`` tables below are the durable backing for that repository as
# it exists today - one table per record type in ``app.api.repo``, field for
# field. They are deliberately conservative:
#
#   * TEXT primary keys, because the repository mints ``str(uuid4())`` ids and
#     hands them back as strings;
#   * ``_JSON`` (JSONB on PostgreSQL, JSON elsewhere) for every pydantic
#     payload, so adding a field to StoryGraph / SceneTiming / SceneShotList /
#     SceneRenderSettings does not need a migration;
#   * ``LargeBinary`` (BYTEA / BLOB) for rendered WAV, video and frame bytes;
#   * no foreign keys. ``InMemoryRepository`` happily stores a shot list for a
#     project id that does not exist, and the conformance suite pins that both
#     implementations behave identically - referential integrity here would be
#     a behavioural difference, not a safety net.
#
# When the normalized schema above is finally populated by the ingest pipeline,
# these tables become the migration source, not a competitor.

# JSONB on PostgreSQL (indexable, binary) and plain JSON everywhere else, which
# is what lets the conformance suite run this schema on SQLite.
_JSON = JSON().with_variant(JSONB(), "postgresql")


def _text_pk() -> Mapped[str]:
    """Client-generated uuid4 string, matching ``app.api.repo`` record ids."""
    return mapped_column(Text, primary_key=True)


_utcnow_lock = threading.Lock()
_utcnow_last: datetime | None = None


def _utcnow() -> datetime:
    """UTC now, strictly increasing across calls within this process.

    ``SqlAlchemyRepository.get_user_by_email`` breaks a duplicate-registration
    tie by ordering on ``created_at`` (last registration wins, matching
    ``InMemoryRepository``'s dict overwrite) - the primary key is a
    client-generated uuid4 string, so it carries no chronological signal to
    fall back on. Two rows minted from this default used to be able to land on
    the exact same wall-clock microsecond (observed in practice: two
    back-to-back ``create_user`` calls on this platform can both read
    ``datetime.now()`` before the clock advances), which made "last one wins"
    resolve arbitrarily by uuid string instead. Bumping a tied reading forward
    by a microsecond keeps every timestamp this process hands out unique and
    in call order, so that ordering is unambiguous again. This only holds
    within one process - genuinely simultaneous writes from two different
    Cloud Run instances have no single canonical "latest" regardless of how
    this clock is implemented.
    """
    global _utcnow_last
    with _utcnow_lock:
        now = datetime.now(timezone.utc)
        if _utcnow_last is not None and now <= _utcnow_last:
            now = _utcnow_last + timedelta(microseconds=1)
        _utcnow_last = now
        return now


def _touched_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class StoredUser(Base):
    """``app.api.repo.UserRecord``."""

    __tablename__ = "store_users"
    __table_args__ = (Index("ix_store_users_email_lower", "email_lower"),)

    id: Mapped[str] = _text_pk()
    email: Mapped[str] = mapped_column(Text, nullable=False)
    # Case-folded lookup key mirroring InMemoryRepository's ``_users_by_email``.
    # Not unique: the in-memory repository accepts a second registration for the
    # same address (the auth router is what rejects it), so a unique index here
    # would raise where the other implementation returns a record.
    email_lower: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    salt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class StoredProject(Base):
    """``app.api.repo.ProjectRecord``."""

    __tablename__ = "store_projects"
    __table_args__ = (Index("ix_store_projects_owner_id", "owner_id"),)

    id: Mapped[str] = _text_pk()
    owner_id: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    grammar_profile: Mapped[str] = mapped_column(Text, nullable=False)
    validator_mode: Mapped[str] = mapped_column(Text, nullable=False)
    rights_attested: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cost_cap_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_spent_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class StoredScript(Base):
    """``app.api.repo.ScriptRecord`` - one (latest) script per project."""

    __tablename__ = "store_scripts"
    __table_args__ = (
        UniqueConstraint("project_id", name="uq_store_scripts_project_id"),
    )

    id: Mapped[str] = _text_pk()
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    updated_at: Mapped[datetime] = _touched_at()


class StoredShotList(Base):
    """``app.api.repo.ShotListRecord`` - one per (project, scene)."""

    __tablename__ = "store_shotlists"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "scene_ordinal", name="uq_store_shotlists_project_scene"
        ),
    )

    id: Mapped[str] = _text_pk()
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    shotlist: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    updated_at: Mapped[datetime] = _touched_at()


class StoredFinding(Base):
    """``app.api.repo.FindingRecord``.

    ``position`` is the index within the ``replace_findings`` batch that wrote
    the row; ordering by it reproduces the insertion order that
    ``InMemoryRepository.list_findings`` returns from its dict.
    """

    __tablename__ = "store_findings"
    __table_args__ = (
        Index(
            "ix_store_findings_project_id_scene_ordinal", "project_id", "scene_ordinal"
        ),
    )

    id: Mapped[str] = _text_pk()
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_code: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    shot_ordinal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    deliberate: Mapped[bool] = mapped_column(Boolean, nullable=False)
    deliberate_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class StoredAudioRender(Base):
    """``app.api.repo.AudioRenderRecord`` - one (latest) render per scene."""

    __tablename__ = "store_audio_renders"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "scene_ordinal", name="uq_store_audio_renders_project_scene"
        ),
    )

    id: Mapped[str] = _text_pk()
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    wav_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    clip_count: Mapped[int] = mapped_column(Integer, nullable=False)
    ambience_tags: Mapped[list[str]] = mapped_column(_JSON, nullable=False)
    timing: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False)
    stale_reasons: Mapped[list[str]] = mapped_column(_JSON, nullable=False)
    updated_at: Mapped[datetime] = _touched_at()


class StoredRenderSettings(Base):
    """``app.render.audio.model.SceneRenderSettings`` per (project, scene).

    An absent row means "engine defaults"; the repository never writes a row it
    was not explicitly asked to save.
    """

    __tablename__ = "store_render_settings"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "scene_ordinal", name="uq_store_render_settings_project_scene"
        ),
    )

    id: Mapped[str] = _text_pk()
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    settings: Mapped[dict[str, Any]] = mapped_column(_JSON, nullable=False)
    updated_at: Mapped[datetime] = _touched_at()


class StoredVideoRender(Base):
    """``app.api.repo.VideoRenderRecord`` - latest per (project, scene, shot).

    ``video_bytes`` is empty when the provider returned URLs only; both cases
    are stored so a redeploy does not lose a render that cost real money.
    """

    __tablename__ = "store_video_renders"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "scene_ordinal",
            "shot_ordinal",
            name="uq_store_video_renders_project_scene_shot",
        ),
    )

    id: Mapped[str] = _text_pk()
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    shot_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    video_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    output_urls: Mapped[list[str]] = mapped_column(_JSON, nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = _touched_at()


class StoredShotFrame(Base):
    """Previz board/frame image bytes per (project, scene, shot)."""

    __tablename__ = "store_shot_frames"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "scene_ordinal",
            "shot_ordinal",
            name="uq_store_shot_frames_project_scene_shot",
        ),
    )

    id: Mapped[str] = _text_pk()
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    scene_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    shot_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    image_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    updated_at: Mapped[datetime] = _touched_at()


#: Every table the API record store owns. ``app.db.repository`` and the
#: conformance suite create exactly these - the normalized PRD tables above use
#: PostgreSQL-only types (pg enums, arrays, int4range, gist exclusion) and
#: cannot be emitted on any other dialect.
STORE_TABLES = [
    StoredUser.__table__,
    StoredProject.__table__,
    StoredScript.__table__,
    StoredShotList.__table__,
    StoredFinding.__table__,
    StoredAudioRender.__table__,
    StoredRenderSettings.__table__,
    StoredVideoRender.__table__,
    StoredShotFrame.__table__,
]
