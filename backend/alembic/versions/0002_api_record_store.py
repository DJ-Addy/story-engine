"""API record store: durable backing for app.api.repo.Repository.

Migration 0001 builds the normalized PRD schema, which nothing writes to yet.
Everything the running API persists - users, projects, story graphs, shot
lists, continuity findings, audio renders and their timing, per-scene render
settings, video renders, previz frames - lived only in
``InMemoryRepository``'s dicts, so a Cloud Run restart or redeploy erased it.

This revision adds the ``store_*`` tables that
``app.db.repository.SqlAlchemyRepository`` reads and writes, one per record
type in ``app.api.repo``.

Two deliberate choices, both explained at length in ``app/db/models.py``:

* TEXT primary keys, because the repository mints and returns ``uuid4``
  strings rather than letting the database generate ids;
* no foreign keys between these tables. ``InMemoryRepository`` will store a
  shot list under a project id that never existed, and the conformance suite
  pins both implementations to identical behaviour - a FK here would be a
  behavioural difference, not a safety net. Cleanup is by project id.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)


def _text_pk() -> sa.Column:
    return sa.Column("id", sa.Text(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "store_users",
        _text_pk(),
        sa.Column("email", sa.Text(), nullable=False),
        # Case-folded lookup key. Intentionally not unique: the in-memory
        # repository accepts a duplicate registration (the auth router is what
        # returns 409), so a unique index would raise where it does not.
        sa.Column("email_lower", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("salt", sa.Text(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_store_users"),
    )
    op.create_index("ix_store_users_email_lower", "store_users", ["email_lower"])

    op.create_table(
        "store_projects",
        _text_pk(),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("grammar_profile", sa.Text(), nullable=False),
        sa.Column("validator_mode", sa.Text(), nullable=False),
        sa.Column("rights_attested", sa.Boolean(), nullable=False),
        sa.Column("cost_cap_cents", sa.Integer(), nullable=False),
        sa.Column("cost_spent_cents", sa.Integer(), nullable=False),
        sa.Column("created_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_store_projects"),
    )
    op.create_index("ix_store_projects_owner_id", "store_projects", ["owner_id"])

    op.create_table(
        "store_scripts",
        _text_pk(),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        # The whole StoryGraph IR as one document: the API reads and rewrites it
        # atomically, and JSONB keeps it queryable without a migration every
        # time the IR grows a field.
        sa.Column("graph", JSONB(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_store_scripts"),
        sa.UniqueConstraint("project_id", name="uq_store_scripts_project_id"),
    )

    op.create_table(
        "store_shotlists",
        _text_pk(),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("scene_ordinal", sa.Integer(), nullable=False),
        sa.Column("shotlist", JSONB(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_store_shotlists"),
        sa.UniqueConstraint(
            "project_id", "scene_ordinal", name="uq_store_shotlists_project_scene"
        ),
    )

    op.create_table(
        "store_findings",
        _text_pk(),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("scene_ordinal", sa.Integer(), nullable=False),
        # Index within the replace_findings batch that wrote the row; ordering
        # by it reproduces in-memory insertion order.
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("shot_ordinal", sa.Integer(), nullable=True),
        sa.Column("deliberate", sa.Boolean(), nullable=False),
        sa.Column("deliberate_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_store_findings"),
    )
    op.create_index(
        "ix_store_findings_project_id_scene_ordinal",
        "store_findings",
        ["project_id", "scene_ordinal"],
    )

    op.create_table(
        "store_audio_renders",
        _text_pk(),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("scene_ordinal", sa.Integer(), nullable=False),
        sa.Column("wav_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("clip_count", sa.Integer(), nullable=False),
        sa.Column("ambience_tags", JSONB(), nullable=False),
        sa.Column("timing", JSONB(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("stale_reasons", JSONB(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_store_audio_renders"),
        sa.UniqueConstraint(
            "project_id", "scene_ordinal", name="uq_store_audio_renders_project_scene"
        ),
    )

    op.create_table(
        "store_render_settings",
        _text_pk(),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("scene_ordinal", sa.Integer(), nullable=False),
        sa.Column("settings", JSONB(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_store_render_settings"),
        sa.UniqueConstraint(
            "project_id", "scene_ordinal", name="uq_store_render_settings_project_scene"
        ),
    )

    op.create_table(
        "store_video_renders",
        _text_pk(),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("scene_ordinal", sa.Integer(), nullable=False),
        sa.Column("shot_ordinal", sa.Integer(), nullable=False),
        # Empty when the provider returned URLs only; stored either way so a
        # redeploy cannot lose a render that cost real money.
        sa.Column("video_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("output_urls", JSONB(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("cost_cents", sa.Integer(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_store_video_renders"),
        sa.UniqueConstraint(
            "project_id",
            "scene_ordinal",
            "shot_ordinal",
            name="uq_store_video_renders_project_scene_shot",
        ),
    )

    op.create_table(
        "store_shot_frames",
        _text_pk(),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("scene_ordinal", sa.Integer(), nullable=False),
        sa.Column("shot_ordinal", sa.Integer(), nullable=False),
        sa.Column("image_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_store_shot_frames"),
        sa.UniqueConstraint(
            "project_id",
            "scene_ordinal",
            "shot_ordinal",
            name="uq_store_shot_frames_project_scene_shot",
        ),
    )


def downgrade() -> None:
    op.drop_table("store_shot_frames")
    op.drop_table("store_video_renders")
    op.drop_table("store_render_settings")
    op.drop_table("store_audio_renders")
    op.drop_index("ix_store_findings_project_id_scene_ordinal", "store_findings")
    op.drop_table("store_findings")
    op.drop_table("store_shotlists")
    op.drop_table("store_scripts")
    op.drop_index("ix_store_projects_owner_id", "store_projects")
    op.drop_table("store_projects")
    op.drop_index("ix_store_users_email_lower", "store_users")
    op.drop_table("store_users")
