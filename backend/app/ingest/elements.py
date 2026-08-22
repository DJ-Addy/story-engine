"""Shared contract between screenplay parsers and the normalizer.

Every ingest parser (Fountain, FDX, PDF) emits a flat list of ``RawElement``.
``normalize.py`` turns that list into the story graph (scenes, lines, characters).
Parsers must NOT try to build scenes or resolve characters — that is the
normalizer's job, and keeping parsers dumb keeps them testable per format.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ElementKind(StrEnum):
    SLUGLINE = "slugline"
    ACTION = "action"
    CHARACTER_CUE = "character_cue"
    PARENTHETICAL = "parenthetical"
    DIALOGUE = "dialogue"
    TRANSITION = "transition"
    PAGE_BREAK = "page_break"
    UNKNOWN = "unknown"


class RawElement(BaseModel):
    kind: ElementKind
    text: str
    # Source position for error reporting and manual-correction round-trips.
    source_line: int | None = None
    page: float | None = None
    # Character cue metadata, populated only for kind == CHARACTER_CUE.
    # 'BOB (V.O.)' -> cue_name='BOB', cue_extension='V.O.'
    cue_name: str | None = None
    cue_extension: str | None = None
    is_dual_dialogue: bool = False


class ParsedScene(BaseModel):
    ordinal: int
    slugline: str | None
    interior: bool | None = None
    location: str | None = None
    time_of_day: str | None = None
    elements: list[RawElement] = Field(default_factory=list)


class AttributedLine(BaseModel):
    """A normalized story-graph line (PRD `lines` table, pre-persistence)."""

    ordinal: int
    kind: str  # narration|dialogue|action|parenthetical|transition
    text: str
    character_name: str | None = None
    attribution_confidence: float | None = None
    attribution_source: str | None = None  # cue|tag|booknlp|llm|manual


class NormalizedScene(BaseModel):
    ordinal: int
    slugline: str | None
    interior: bool | None
    location: str | None
    time_of_day: str | None
    lines: list[AttributedLine] = Field(default_factory=list)


class NormalizedCharacter(BaseModel):
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    line_count: int = 0


class StoryGraph(BaseModel):
    scenes: list[NormalizedScene] = Field(default_factory=list)
    characters: list[NormalizedCharacter] = Field(default_factory=list)
