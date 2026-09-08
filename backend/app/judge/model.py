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
# Casting proposer (voice + tone)
# --------------------------------------------------------------------------- #
# Where a proposed tone came from, and therefore how far it can be trusted:
# "emotion_tags" — the writer wrote the delivery down and ingest captured it
# (a screenplay parenthetical); "text_cues" — it was read off the words of the
# line itself; "none" — there was no evidence, so no tone is proposed at all.
ToneEvidence = Literal["emotion_tags", "text_cues", "none"]


class CastingProposalEntry(BaseModel):
    """One proposed part: who speaks it, in which voice, in what tone.

    ``character is None`` **is** the narrator, deliberately the same sentinel
    the render path already uses (``app.render.audio.timing.speaker_of``
    returns ``None`` for the action and narration a narrator reads), so a
    proposal drops into a voice map without translation. ``is_narrator`` states
    it in the open for consumers that would rather not read a null as a name.

    ``confidence`` rates the *tone*, not the voice, and is exactly 0.0 when
    ``tone is None`` — a part with no tonal evidence has nothing to be
    confident about, and a number invented to fill the field would be the one
    part of this model a director could not check. The voice choice carries its
    own number in ``voice_fit`` (the deterministic fit score from
    :mod:`app.judge.voices`), so the two can be weighed separately instead of
    blended into one figure that hides which half was weak.
    """

    character: str | None = None
    is_narrator: bool = False
    voice_id: str
    voice_name: str
    tone: str | None = None
    tone_evidence: ToneEvidence = "none"
    confidence: float = Field(ge=0.0, le=1.0)
    voice_fit: float = Field(ge=0.0, le=1.0)
    line_count: int = 0
    rationale: str


class CastingProposal(BaseModel):
    """A whole proposed cast: the narrator first, then every character.

    Characters follow in casting order (most dialogue first, ties by name),
    which is the order the voices were dealt in — reading the list top to
    bottom is reading the reasoning in the order it happened.
    """

    entries: list[CastingProposalEntry] = Field(default_factory=list)
    rationale: str = ""

    def as_casting(self) -> dict[str, str]:
        """Character name -> voice id, with the narrator left out.

        The shape the rest of the system already speaks (``app.agents.tools``'
        working casting, the scene renderer's voice map), so a proposal can be
        handed straight to the voice-fit judge or to a render without any
        consumer needing to know this model.
        """
        return {e.character: e.voice_id for e in self.entries if e.character is not None}


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
