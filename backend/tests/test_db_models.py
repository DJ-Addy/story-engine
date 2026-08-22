"""Structure tests for the Story Engine persistence layer (PRD §3.2).

No live database: everything is asserted against SQLAlchemy metadata, plus
DDL compiled for the PostgreSQL dialect (compilation does not connect).
"""

from sqlalchemy import Table, UniqueConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db import models  # noqa: F401  (registers all models on Base)
from app.db.base import Base

EXPECTED_TABLES = {
    "users",
    "projects",
    "scripts",
    "characters",
    "reference_sets",
    "reference_images",
    "character_variants",
    "scenes",
    "lines",
    "shots",
    "continuity_findings",
    "audio_clips",
    "jobs",
}


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def has_unique(t: Table, *columns: str) -> bool:
    wanted = set(columns)
    return any(
        isinstance(c, UniqueConstraint) and {col.name for col in c.columns} == wanted
        for c in t.constraints
    )


# --------------------------------------------------------------------------
# Tables and columns
# --------------------------------------------------------------------------


def test_all_thirteen_tables_registered():
    assert EXPECTED_TABLES <= set(Base.metadata.tables)
    assert len(EXPECTED_TABLES) == 13


def test_uuid_primary_keys_default_gen_random_uuid():
    for name in EXPECTED_TABLES:
        pk_cols = list(table(name).primary_key.columns)
        assert len(pk_cols) == 1, name
        col = pk_cols[0]
        assert col.name == "id", name
        assert col.server_default is not None, name
        assert "gen_random_uuid" in str(col.server_default.arg), name


def test_projects_cost_cap_cents_default():
    col = table("projects").c.cost_cap_cents
    assert col.nullable is False
    assert col.server_default is not None
    assert str(col.server_default.arg).strip() == "15000"


def test_scripts_format_uses_source_format_enum():
    col = table("scripts").c.format
    assert isinstance(col.type, postgresql.ENUM)
    assert col.type.name == "source_format"
    assert set(col.type.enums) == {"fountain", "fdx", "pdf_screenplay", "epub", "txt"}


def test_cascading_foreign_keys():
    cascade_edges = {
        ("scripts", "project_id"),
        ("characters", "project_id"),
        ("reference_images", "reference_set_id"),
        ("character_variants", "character_id"),
        ("scenes", "script_id"),
        ("lines", "scene_id"),
        ("shots", "scene_id"),
        ("continuity_findings", "scene_id"),
        ("continuity_findings", "shot_id"),
        ("audio_clips", "line_id"),
        ("audio_clips", "scene_id"),
        ("jobs", "project_id"),
    }
    for table_name, column_name in cascade_edges:
        fks = list(table(table_name).c[column_name].foreign_keys)
        assert fks, (table_name, column_name)
        assert fks[0].ondelete == "CASCADE", (table_name, column_name)


# --------------------------------------------------------------------------
# Unique constraints
# --------------------------------------------------------------------------


def test_unique_constraints():
    assert has_unique(table("characters"), "project_id", "canonical_name")
    assert has_unique(table("scenes"), "script_id", "ordinal")
    assert has_unique(table("lines"), "scene_id", "ordinal")
    assert has_unique(table("shots"), "scene_id", "ordinal")


def test_jobs_idempotency_key_unique_and_nullable():
    col = table("jobs").c.idempotency_key
    assert col.unique is True
    assert col.nullable is True


def test_users_email_unique_not_null():
    col = table("users").c.email
    assert col.unique is True
    assert col.nullable is False


# --------------------------------------------------------------------------
# Exclusion constraint on character_variants
# --------------------------------------------------------------------------


def test_character_variants_has_exclude_constraint():
    excludes = [
        c for c in table("character_variants").constraints
        if isinstance(c, ExcludeConstraint)
    ]
    assert len(excludes) == 1
    assert excludes[0].using == "gist"


def test_character_variants_ddl_emits_exclude_using_gist():
    ddl = str(
        CreateTable(table("character_variants")).compile(dialect=postgresql.dialect())
    )
    assert "EXCLUDE USING gist" in ddl
    assert "character_id WITH =" in ddl
    assert "int4range(scene_from, COALESCE(scene_to, 2147483647)) WITH &&" in ddl


# --------------------------------------------------------------------------
# Indexes
# --------------------------------------------------------------------------


def test_composite_indexes_exist():
    expected = {
        "lines": ("ix_lines_scene_id_ordinal", {"scene_id", "ordinal"}),
        "shots": ("ix_shots_scene_id_ordinal", {"scene_id", "ordinal"}),
        "jobs": ("ix_jobs_project_id_state", {"project_id", "state"}),
    }
    for table_name, (index_name, column_names) in expected.items():
        indexes = {i.name: i for i in table(table_name).indexes}
        assert index_name in indexes, table_name
        assert {c.name for c in indexes[index_name].columns} == column_names


def test_continuity_findings_partial_index_has_whereclause():
    indexes = {i.name: i for i in table("continuity_findings").indexes}
    idx = indexes["ix_continuity_findings_scene_id_active"]

    where = idx.dialect_options["postgresql"]["where"]
    assert where is not None
    assert "deliberate" in str(where)

    ddl = str(CreateIndex(idx).compile(dialect=postgresql.dialect()))
    assert "WHERE deliberate = false" in ddl
