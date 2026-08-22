"""Story Engine persistence models (PRD §3.2), targeting PostgreSQL 16.

SQLAlchemy 2.0 declarative style (Mapped / mapped_column). All primary keys
are UUIDs generated server-side via gen_random_uuid(); timestamps are
timestamptz. Schema creation is handled by Alembic (see
backend/alembic/versions/0001_initial_schema.py), which also creates the
btree_gist extension required by the character_variants exclusion constraint.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
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
