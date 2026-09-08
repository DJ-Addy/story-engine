"""Casting store: the voice and tone decided for each speaker.

The voice-fit judge and the audio renderer were two unrelated opinions about
the same scene. The judge scored a casting the caller handed it and discarded
it; the renderer dealt voices round-robin from whatever the TTS provider
published and never saw the judge at all. Nothing in ``app.api.repo`` could
hold a casting, so there was nowhere for the decision to live between them.

This revision adds that place. One row per project, the entries stored whole as
JSON - see ``app/db/models.py`` for why a row per character would buy nothing
here.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TIMESTAMPTZ = sa.TIMESTAMP(timezone=True)


def upgrade() -> None:
    op.create_table(
        "store_castings",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Text(), nullable=False),
        sa.Column("entries", JSONB(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("updated_at", TIMESTAMPTZ, nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_store_castings"),
        sa.UniqueConstraint("project_id", name="uq_store_castings_project"),
    )


def downgrade() -> None:
    op.drop_table("store_castings")
