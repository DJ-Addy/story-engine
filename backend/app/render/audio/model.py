"""Domain models for the audio render planner (no audio processing here)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["narration", "dialogue"]


class SpeechClip(BaseModel):
    """One rendered speech clip in script order.

    ``character_name is None`` means the narrator. ``block_id`` groups
    consecutive clips belonging to a single speech block.
    """

    line_ordinal: int
    character_name: str | None = None
    duration_ms: int = Field(ge=0)
    beat_index: int
    scene_ordinal: int
    block_id: int

    @property
    def role(self) -> Role:
        return "narration" if self.character_name is None else "dialogue"


class ClipRef(BaseModel):
    """Reference to a clip plus the boundary metadata used for gap planning."""

    line_ordinal: int
    character_name: str | None
    role: Role
    duration_ms: int
    beat_index: int
    scene_ordinal: int
    block_id: int

    @classmethod
    def from_clip(cls, clip: SpeechClip) -> ClipRef:
        return cls(
            line_ordinal=clip.line_ordinal,
            character_name=clip.character_name,
            role=clip.role,
            duration_ms=clip.duration_ms,
            beat_index=clip.beat_index,
            scene_ordinal=clip.scene_ordinal,
            block_id=clip.block_id,
        )


class TimelineEntry(BaseModel):
    clip_index: int
    clip: ClipRef
    start_ms: int


class SpeechBusPlan(BaseModel):
    entries: list[TimelineEntry]
    total_ms: int


class RenderedClip(BaseModel):
    """One spoken clip as actually placed on the speech bus, with its onset and
    real (post-TTS) duration. ``character_name is None`` means the narrator or an
    action/narration line. Keyed by ``line_ordinal`` so shot coverage
    (``covers_lines``) can be mapped onto clip timings."""

    line_ordinal: int
    kind: str  # dialogue|narration|action
    character_name: str | None = None
    emotion: str | None = None
    text: str
    start_ms: int
    duration_ms: int = Field(ge=0)


class RenderedSfx(BaseModel):
    """A foreground SFX event and the time it was placed on the SFX bus."""

    at_ms: int
    name: str


class SceneTiming(BaseModel):
    """Time-aligned placement metadata for a single scene render.

    Computed by the same render pass that produces the WAV (so the onsets line
    up with the audio to the millisecond) but carries no audio bytes. The API
    layer projects this — plus the scene's shot list — into a ``SceneTimeline``.
    """

    scene_ordinal: int
    duration_ms: int
    clips: list[RenderedClip] = Field(default_factory=list)
    sfx: list[RenderedSfx] = Field(default_factory=list)
    ambience_tags: list[str] = Field(default_factory=list)
