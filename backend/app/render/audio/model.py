"""Domain models for the audio render planner (no audio processing here)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["narration", "dialogue"]

# Silence left after the last spoken clip so the ambience bed can breathe. It
# lives here rather than in the renderer because the estimator
# (:mod:`app.render.audio.estimate`) must add the same tail to produce a scene
# length comparable with a real render's.
SPEECH_TAIL_MS = 1200


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


# ``dsp.duck``'s own default compressor ratio. Kept as a literal rather than an
# import so this persisted-settings module stays free of the numpy DSP stack;
# test_timeline_edits pins the two together.
_DUCK_RATIO_AT_DEFAULT = 6.0


class SceneRenderSettings(BaseModel):
    """Per-scene render knobs the timeline editor writes and the renderer honors.

    Defaults reproduce the pre-settings render exactly: ``pacing`` 1.0 leaves the
    PRD gap table untouched and ``ambience_duck`` 0.5 maps to the DSP's own
    default ducking ratio, so a scene nobody has edited renders byte-identical
    audio.
    """

    model_config = ConfigDict(frozen=True)

    # Multiplies every inter-clip gap on the speech bus. <1 tightens the scene
    # ("tighten pacing"), >1 lets it breathe. Clip durations are TTS output and
    # are never scaled — pacing is dead air only.
    pacing: float = Field(default=1.0, ge=0.25, le=4.0)
    # How hard ambience ducks under speech, 0 (no ducking at all) to 1 (hardest).
    ambience_duck: float = Field(default=0.5, ge=0.0, le=1.0)

    @property
    def duck_ratio(self) -> float:
        """``ambience_duck`` as a compressor ratio for ``dsp.duck``.

        Linear from 1.0 (ratio 1 = unity gain = no duck) through the DSP default
        at the 0.5 midpoint, so the default depth is a no-op on the audio.
        """
        return 1.0 + self.ambience_duck * 2.0 * (_DUCK_RATIO_AT_DEFAULT - 1.0)


DEFAULT_RENDER_SETTINGS = SceneRenderSettings()


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
