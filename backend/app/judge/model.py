"""Result models for the judge layer.

Scores are floats in ``[0, 1]`` — the same convention the IR already uses for
``AttributedLine.attribution_confidence`` — rounded to three decimals so results
are stable and diff-friendly. Findings reuse the continuity validator's
``info | warn | error`` severity vocabulary.
"""

from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

Severity = Literal["info", "warn", "error"]


# --------------------------------------------------------------------------- #
# Voice-fit judge
# --------------------------------------------------------------------------- #
class VoiceFinding(BaseModel):
    """A concrete, actionable mismatch between a character and its voice."""

    code: str
    severity: Severity = "warn"
    message: str


class VoiceSuggestion(BaseModel):
    """An alternative voice from the available pool that fits better."""

    voice_id: str
    voice_name: str
    score: float = Field(ge=0.0, le=1.0)


class CharacterVoiceFit(BaseModel):
    """How well one character's assigned voice fits that character."""

    character: str
    voice_id: str
    voice_name: str
    score: float = Field(ge=0.0, le=1.0)
    rationale: str
    line_count: int
    speaks_share: float = Field(ge=0.0, le=1.0)
    dominant_emotions: list[str] = Field(default_factory=list)
    findings: list[VoiceFinding] = Field(default_factory=list)
    suggestions: list[VoiceSuggestion] = Field(default_factory=list)


class VoiceFitResult(BaseModel):
    """Overall casting evaluation across every cast character."""

    overall_score: float = Field(ge=0.0, le=1.0)
    rationale: str
    characters: list[CharacterVoiceFit] = Field(default_factory=list)
    uncast_characters: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Animatic / shot-list judge
# --------------------------------------------------------------------------- #
class AnimaticFinding(BaseModel):
    """A concrete quality issue in the previz coverage."""

    code: str
    severity: Severity = "warn"
    message: str
    scene_ordinal: int | None = None
    shot_ordinal: int | None = None


class SceneAnimaticScore(BaseModel):
    """Per-scene breakdown of the four animatic quality axes."""

    scene_ordinal: int
    score: float = Field(ge=0.0, le=1.0)
    coverage_score: float = Field(ge=0.0, le=1.0)
    continuity_score: float = Field(ge=0.0, le=1.0)
    variety_score: float = Field(ge=0.0, le=1.0)
    pacing_score: float = Field(ge=0.0, le=1.0)
    shot_count: int
    findings: list[AnimaticFinding] = Field(default_factory=list)


class AnimaticJudgment(BaseModel):
    """Overall animatic evaluation across every scene that has a shot list."""

    overall_score: float = Field(ge=0.0, le=1.0)
    rationale: str
    coverage_score: float = Field(ge=0.0, le=1.0)
    continuity_score: float = Field(ge=0.0, le=1.0)
    variety_score: float = Field(ge=0.0, le=1.0)
    pacing_score: float = Field(ge=0.0, le=1.0)
    scenes: list[SceneAnimaticScore] = Field(default_factory=list)
    findings: list[AnimaticFinding] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Generic ranking leaderboard
# --------------------------------------------------------------------------- #
# A reusable "pick the best" result: submit several candidate variants, run a
# judge on each, and rank them by overall score. Parametrized by whatever judge
# result the entries carry (``VoiceFitResult``, ``AnimaticJudgment``, ...), so
# the same shape backs every ranking endpoint and a later human-voting layer.
RankedResultT = TypeVar("RankedResultT")


class RankedEntry(BaseModel, Generic[RankedResultT]):
    """One candidate's place on the leaderboard, with its full judge result."""

    label: str
    rank: int = Field(ge=1)  # 1-based; 1 is the best-scoring candidate
    overall_score: float = Field(ge=0.0, le=1.0)
    result: RankedResultT


class RankingResult(BaseModel, Generic[RankedResultT]):
    """A leaderboard of judged candidates, best first, with the winning label."""

    winner: str | None = None
    entries: list[RankedEntry[RankedResultT]] = Field(default_factory=list)
