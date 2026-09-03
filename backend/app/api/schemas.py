"""Pydantic request/response models for the API layer.

Story-graph payloads reuse the ingest models directly (``StoryGraphOut``),
and shot payloads reuse ``app.shotlist.schema.ShotSpec`` so the API contract
stays in lockstep with the domain modules.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.adapters.base import Voice
from app.ingest.elements import StoryGraph
from app.judge.model import AnimaticJudgment, RankingResult, VoiceFitResult
from app.judge.ranking import AnimaticCandidate, VoiceCandidate
from app.shotlist.schema import ShotSpec

GrammarProfile = Literal["classical", "handheld", "symmetrical", "anime"]
ValidatorMode = Literal["strict", "lenient", "off"]


class UserCreate(BaseModel):
    email: str = Field(min_length=3, pattern=r".+@.+")
    password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: str
    email: str


class TokenPair(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1)
    grammar_profile: GrammarProfile = "classical"
    validator_mode: ValidatorMode = "strict"
    rights_attested: bool


class ProjectOut(BaseModel):
    id: str
    owner_id: str
    title: str
    grammar_profile: str
    validator_mode: str
    rights_attested: bool
    cost_cap_cents: int = 15000
    cost_spent_cents: int = 0


class ScriptUploadOut(BaseModel):
    script_id: str
    format: str
    scene_count: int
    character_count: int


class NovelIngestOut(BaseModel):
    """Response for POST .../novel: persisted-script stats plus the novel
    conversion's own attribution stats (PRD phase 2)."""

    script_id: str
    scene_count: int
    character_count: int
    quotes: int
    attributed: int
    needs_review: int
    characters: list[str]


class NovelPreviewOut(BaseModel):
    """Response for POST .../novel/preview: conversion stats plus the
    generated Fountain text, without persisting anything."""

    quotes: int
    attributed: int
    needs_review: int
    characters: list[str]
    fountain_text: str


class AudioRenderOut(BaseModel):
    scene_ordinal: int
    duration_ms: int
    clip_count: int
    ambience_tags: list[str]


class TimelineDialogueClip(BaseModel):
    """A placed spoken clip on the dialogue lane. Carries every rendered spoken
    line (dialogue, narration and action), keyed by ``line_ordinal`` so shot
    coverage can be mapped onto it; ``character`` is None for the narrator or an
    action/narration line."""

    line_ordinal: int
    start_ms: int
    duration_ms: int
    character: str | None
    emotion: str | None
    text: str


class TimelineAmbienceSpan(BaseModel):
    start_ms: int
    duration_ms: int
    tag: str


class TimelineSfxMarker(BaseModel):
    at_ms: int
    name: str


class TimelineVisualClip(BaseModel):
    """A shot placed on the visual lane, spanning the dialogue clips its
    ``covers_lines`` reference."""

    start_ms: int
    duration_ms: int
    shot_ordinal: int
    size: str
    subjects: list[str]


class SceneMarker(BaseModel):
    scene_ordinal: int
    start_ms: int
    slugline: str | None = None


class SceneTimeline(BaseModel):
    """Structured, time-aligned lane data for one scene render, so a scrubbable
    frontend timeline can line up with the rendered WAV. The dialogue/ambience/
    sfx lanes come from the render pass; the visual lane is projected from the
    scene's current shot list."""

    scene_ordinal: int
    duration_ms: int
    markers: list[SceneMarker]
    dialogue: list[TimelineDialogueClip]
    ambience: list[TimelineAmbienceSpan]
    sfx: list[TimelineSfxMarker]
    visual: list[TimelineVisualClip]


class VideoRenderRequest(BaseModel):
    """Body for POST .../render/video: which shot to render and how long. The
    shot is addressed by (scene_ordinal, shot_ordinal); ``duration_s`` is the
    requested clip length passed to the video provider."""

    scene_ordinal: int = Field(ge=1)
    shot_ordinal: int = Field(ge=1)
    duration_s: int = Field(default=5, ge=1, le=60)


class VideoRenderOut(BaseModel):
    scene_ordinal: int
    shot_ordinal: int
    duration_ms: int
    cost_cents: int
    provider: str
    model: str
    source: Literal["image", "text"]  # which generation path was taken
    output_urls: list[str]
    has_video: bool  # whether downloaded clip bytes are stored


class StoryGraphOut(StoryGraph):
    """Response model for GET .../graph; identical shape to the ingest model."""


class LinePatch(BaseModel):
    """Manual attribution correction. Applying any patch stamps the line with
    attribution_source='manual' and confidence 1.0 (human edits are ground truth)."""

    character_name: str | None = None
    text: str | None = None

    @model_validator(mode="after")
    def _require_some_change(self) -> "LinePatch":
        if self.character_name is None and self.text is None:
            raise ValueError("patch must set character_name and/or text")
        return self


class ShotListOut(BaseModel):
    scene_ordinal: int
    action_axis: str
    shots: list[ShotSpec]


class FindingOut(BaseModel):
    id: str
    rule_code: str
    severity: str
    message: str
    shot_ordinal: int | None = None
    deliberate: bool = False
    deliberate_note: str | None = None


class FindingPatch(BaseModel):
    deliberate: bool
    deliberate_note: str | None = None


class VoiceFitRequest(BaseModel):
    """Body for POST .../judge/voices.

    ``casting`` maps a character's canonical name to its assigned voice (a
    ``Voice`` carries id/name/tags — the same shape the TTS adapters expose).
    ``available_voices`` is the pool alternative suggestions are drawn from; when
    omitted it defaults to the distinct voices already used in the casting.
    """

    casting: dict[str, Voice] = Field(min_length=1)
    available_voices: list[Voice] | None = None


class VoiceFitOut(VoiceFitResult):
    """Response for POST .../judge/voices; identical shape to the judge model."""


class AnimaticJudgmentOut(AnimaticJudgment):
    """Response for POST .../judge/animatic; identical shape to the judge model."""


def _require_unique_labels(labels: list[str]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for label in labels:
        (duplicates if label in seen else seen).add(label)
    if duplicates:
        raise ValueError(
            f"candidate labels must be unique; duplicated: {', '.join(sorted(duplicates))}"
        )


class VoiceRankRequest(BaseModel):
    """Body for POST .../judge/rank/voices.

    ``candidates`` is a non-empty list of casting variants, each with a unique
    ``label``; they are judged against the project's IR and ranked best-first.
    ``available_voices`` is the shared suggestion pool applied to every candidate.
    """

    candidates: list[VoiceCandidate] = Field(min_length=1)
    available_voices: list[Voice] | None = None

    @model_validator(mode="after")
    def _unique_labels(self) -> "VoiceRankRequest":
        _require_unique_labels([c.label for c in self.candidates])
        return self


class AnimaticRankRequest(BaseModel):
    """Body for POST .../judge/rank/animatic.

    ``candidates`` is a non-empty list of animatic variants, each with a unique
    ``label`` and its own set of scene shot lists (the same ``SceneShotList``
    shape the shot-list endpoints use). Each variant is scored and ranked.
    """

    candidates: list[AnimaticCandidate] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_labels(self) -> "AnimaticRankRequest":
        _require_unique_labels([c.label for c in self.candidates])
        return self


class VoiceRankingOut(RankingResult[VoiceFitResult]):
    """Response for POST .../judge/rank/voices; a leaderboard of casting variants."""


class AnimaticRankingOut(RankingResult[AnimaticJudgment]):
    """Response for POST .../judge/rank/animatic; a leaderboard of animatic variants."""
