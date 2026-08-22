"""Initial Story Engine schema (PRD §3.2).

Creates the btree_gist extension, all native enum types, the 13 core
tables, unique/exclusion constraints, and indexes.

Revision ID: 0001
Revises:
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enum types are created explicitly below; create_type=False stops
# create_table from trying to create them a second time.
source_format = postgresql.ENUM(
    "fountain", "fdx", "pdf_screenplay", "epub", "txt",
    name="source_format", create_type=False,
)
line_kind = postgresql.ENUM(
    "narration", "dialogue", "action", "parenthetical", "transition",
    name="line_kind", create_type=False,
)
shot_size = postgresql.ENUM(
    "ecu", "cu", "mcu", "ms", "mws", "ws", "ews", "insert", "pov",
    name="shot_size", create_type=False,
)
axis_side = postgresql.ENUM(
    "a", "b", "neutral", "crossing",
    name="axis_side", create_type=False,
)
job_state = postgresql.ENUM(
    "queued", "running", "succeeded", "failed", "cancelled",
    name="job_state", create_type=False,
)

ALL_ENUMS = (source_format, line_kind, shot_size, axis_side, job_state)

UUID = postgresql.UUID(as_uuid=True)
TIMESTAMPTZ = postgresql.TIMESTAMP(timezone=True)


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id", UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    bind = op.get_bind()
    for enum in ALL_ENUMS:
        enum.create(bind, checkfirst=True)

    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column(
            "created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "projects",
        _uuid_pk(),
        sa.Column("owner_id", UUID, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column(
            "grammar_profile",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'classical'"),
        ),
        sa.Column(
            "validator_mode",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'strict'"),
        ),
        sa.Column(
            "rights_attested",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "cost_cap_cents",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("15000"),
        ),
        sa.Column(
            "cost_spent_cents",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.id"], name="fk_projects_owner_id_users"
        ),
    )

    op.create_table(
        "scripts",
        _uuid_pk(),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("format", source_format, nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("checksum", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("parsed_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_scripts"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_scripts_project_id_projects",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "characters",
        _uuid_pk(),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column(
            "aliases",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("age_range", postgresql.INT4RANGE(), nullable=True),
        sa.Column(
            "line_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "is_narrator",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_characters"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_characters_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "project_id",
            "canonical_name",
            name="uq_characters_project_id_canonical_name",
        ),
    )

    op.create_table(
        "reference_sets",
        _uuid_pk(),
        sa.Column("seed", sa.BigInteger(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("gen_params", postgresql.JSONB(), nullable=False),
        sa.Column("locked_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_reference_sets"),
    )

    op.create_table(
        "reference_images",
        _uuid_pk(),
        sa.Column("reference_set_id", UUID, nullable=False),
        sa.Column("angle", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column(
            "is_primary",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_reference_images"),
        sa.ForeignKeyConstraint(
            ["reference_set_id"],
            ["reference_sets.id"],
            name="fk_reference_images_reference_set_id_reference_sets",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "character_variants",
        _uuid_pk(),
        sa.Column("character_id", UUID, nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("scene_from", sa.Integer(), nullable=False),
        sa.Column("scene_to", sa.Integer(), nullable=True),
        sa.Column("wardrobe", sa.Text(), nullable=True),
        sa.Column("condition", sa.Text(), nullable=True),
        sa.Column(
            "props",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("reference_set_id", UUID, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_character_variants"),
        sa.ForeignKeyConstraint(
            ["character_id"],
            ["characters.id"],
            name="fk_character_variants_character_id_characters",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reference_set_id"],
            ["reference_sets.id"],
            name="fk_character_variants_reference_set_id_reference_sets",
        ),
        # No two variants of the same character may cover overlapping scene
        # ranges; open-ended ranges extend to INT4 max. Needs btree_gist.
        postgresql.ExcludeConstraint(
            (sa.text("character_id"), "="),
            (
                sa.text("int4range(scene_from, COALESCE(scene_to, 2147483647))"),
                "&&",
            ),
            using="gist",
            name="excl_character_variants_scene_range",
        ),
    )

    op.create_table(
        "scenes",
        _uuid_pk(),
        sa.Column("script_id", UUID, nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("slugline", sa.Text(), nullable=True),
        sa.Column("interior", sa.Boolean(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("time_of_day", sa.Text(), nullable=True),
        sa.Column("weather", sa.Text(), nullable=True),
        sa.Column("mood", sa.Text(), nullable=True),
        sa.Column(
            "ambience_tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column("page_start", sa.Numeric(6, 2), nullable=True),
        sa.Column("page_end", sa.Numeric(6, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_scenes"),
        sa.ForeignKeyConstraint(
            ["script_id"],
            ["scripts.id"],
            name="fk_scenes_script_id_scripts",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("script_id", "ordinal", name="uq_scenes_script_id_ordinal"),
    )

    op.create_table(
        "lines",
        _uuid_pk(),
        sa.Column("scene_id", UUID, nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("kind", line_kind, nullable=False),
        sa.Column("character_id", UUID, nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("emotion", sa.Text(), nullable=True),
        sa.Column("attribution_confidence", postgresql.REAL(), nullable=True),
        sa.Column("attribution_source", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_lines"),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["scenes.id"],
            name="fk_lines_scene_id_scenes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["character_id"],
            ["characters.id"],
            name="fk_lines_character_id_characters",
        ),
        sa.UniqueConstraint("scene_id", "ordinal", name="uq_lines_scene_id_ordinal"),
    )
    op.create_index("ix_lines_scene_id_ordinal", "lines", ["scene_id", "ordinal"])

    op.create_table(
        "shots",
        _uuid_pk(),
        sa.Column("scene_id", UUID, nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("size", shot_size, nullable=False),
        sa.Column(
            "subject_ids",
            postgresql.ARRAY(UUID),
            nullable=False,
            server_default=sa.text("'{}'::uuid[]"),
        ),
        sa.Column(
            "axis_side",
            axis_side,
            nullable=False,
            server_default=sa.text("'neutral'"),
        ),
        sa.Column("lens_mm", postgresql.REAL(), nullable=True),
        sa.Column("camera_height", sa.Text(), nullable=True),
        sa.Column("movement", sa.Text(), nullable=True),
        sa.Column("eyeline", sa.Text(), nullable=True),
        sa.Column("line_id_from", UUID, nullable=True),
        sa.Column("line_id_to", UUID, nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("prompt_text", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_shots"),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["scenes.id"],
            name="fk_shots_scene_id_scenes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["line_id_from"], ["lines.id"], name="fk_shots_line_id_from_lines"
        ),
        sa.ForeignKeyConstraint(
            ["line_id_to"], ["lines.id"], name="fk_shots_line_id_to_lines"
        ),
        sa.UniqueConstraint("scene_id", "ordinal", name="uq_shots_scene_id_ordinal"),
    )
    op.create_index("ix_shots_scene_id_ordinal", "shots", ["scene_id", "ordinal"])

    op.create_table(
        "continuity_findings",
        _uuid_pk(),
        sa.Column("scene_id", UUID, nullable=False),
        sa.Column("shot_id", UUID, nullable=True),
        sa.Column("rule_code", sa.Text(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "deliberate",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("deliberate_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_continuity_findings"),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["scenes.id"],
            name="fk_continuity_findings_scene_id_scenes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["shot_id"],
            ["shots.id"],
            name="fk_continuity_findings_shot_id_shots",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_continuity_findings_scene_id_active",
        "continuity_findings",
        ["scene_id"],
        postgresql_where=sa.text("deliberate = false"),
    )

    op.create_table(
        "audio_clips",
        _uuid_pk(),
        sa.Column("line_id", UUID, nullable=True),
        sa.Column("scene_id", UUID, nullable=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("lufs", postgresql.REAL(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("gen_params", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audio_clips"),
        sa.ForeignKeyConstraint(
            ["line_id"],
            ["lines.id"],
            name="fk_audio_clips_line_id_lines",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["scene_id"],
            ["scenes.id"],
            name="fk_audio_clips_scene_id_scenes",
            ondelete="CASCADE",
        ),
    )

    op.create_table(
        "jobs",
        _uuid_pk(),
        sa.Column("project_id", UUID, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column(
            "state",
            job_state,
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "progress", postgresql.REAL(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "cost_cents", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at", TIMESTAMPTZ, nullable=False, server_default=sa.text("now()")
        ),
        sa.Column("finished_at", TIMESTAMPTZ, nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_jobs_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_jobs_idempotency_key"),
    )
    op.create_index("ix_jobs_project_id_state", "jobs", ["project_id", "state"])


def downgrade() -> None:
    op.drop_table("jobs")
    op.drop_table("audio_clips")
    op.drop_table("continuity_findings")
    op.drop_table("shots")
    op.drop_table("lines")
    op.drop_table("scenes")
    op.drop_table("character_variants")
    op.drop_table("reference_images")
    op.drop_table("reference_sets")
    op.drop_table("characters")
    op.drop_table("scripts")
    op.drop_table("projects")
    op.drop_table("users")

    bind = op.get_bind()
    for enum in ALL_ENUMS:
        enum.drop(bind, checkfirst=True)
