"""Speech-bus timeline planning from the PRD gap table.

Also home to the two rules that decide *what* gets planned — which lines are
spoken at all, and where one speech block ends and the next begins — because
the gap table is applied to block and role boundaries. The renderer
(:mod:`app.render.audio.pipeline`) and the render-free estimator
(:mod:`app.render.audio.estimate`) both build their clips through
:func:`speech_clips` so their onsets differ only by where the durations came
from.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.ingest.elements import AttributedLine, NormalizedScene
from app.render.audio.model import ClipRef, SpeechBusPlan, SpeechClip, TimelineEntry

# Kinds that reach TTS. Parentheticals and transitions are direction, not
# speech: they carry emotion or page furniture and are never synthesized.
SPOKEN_KINDS = frozenset({"dialogue", "action", "narration"})

GAP_INTRA_BLOCK_MS = 180
GAP_SPEAKER_CHANGE_MS = 350
GAP_NARRATION_DIALOGUE_MS = 500
GAP_BEAT_CHANGE_MS = 900
GAP_SCENE_CHANGE_MS = 1600


def gap_between_ms(prev: SpeechClip, cur: SpeechClip) -> int:
    """Gap between two consecutive clips: the largest applicable PRD gap."""
    candidates: list[int] = []
    if cur.block_id == prev.block_id:
        candidates.append(GAP_INTRA_BLOCK_MS)
    if (
        prev.role == "dialogue"
        and cur.role == "dialogue"
        and prev.character_name != cur.character_name
    ):
        candidates.append(GAP_SPEAKER_CHANGE_MS)
    if prev.role != cur.role:
        candidates.append(GAP_NARRATION_DIALOGUE_MS)
    if cur.beat_index != prev.beat_index:
        candidates.append(GAP_BEAT_CHANGE_MS)
    if cur.scene_ordinal != prev.scene_ordinal:
        candidates.append(GAP_SCENE_CHANGE_MS)
    # Same speaker opening a new block with no other boundary crossed:
    # the PRD table has no smaller entry, so fall back to the intra-block gap.
    return max(candidates, default=GAP_INTRA_BLOCK_MS)


def plan_speech_bus(clips: list[SpeechClip], gap_scale: float = 1.0) -> SpeechBusPlan:
    """Place clips sequentially; first clip starts at 0.

    ``gap_scale`` scales every gap from the PRD table (the timeline editor's
    pacing knob). Only dead air moves — clip durations are TTS output. At the
    default 1.0 the arithmetic is exact, so unedited scenes plan identically.
    """
    entries: list[TimelineEntry] = []
    cursor = 0
    for index, clip in enumerate(clips):
        if index > 0:
            cursor += round(gap_between_ms(clips[index - 1], clip) * gap_scale)
        entries.append(
            TimelineEntry(clip_index=index, clip=ClipRef.from_clip(clip), start_ms=cursor)
        )
        cursor += clip.duration_ms
    return SpeechBusPlan(entries=entries, total_ms=cursor)


def spoken_lines(scene: NormalizedScene) -> list[AttributedLine]:
    """The scene's lines that get a voice, in script order."""
    return [line for line in scene.lines if line.kind in SPOKEN_KINDS and line.text.strip()]


def speaker_of(line: AttributedLine) -> str | None:
    """The voice a line is spoken in; ``None`` is the narrator.

    Only dialogue carries a character — action and narration are read by the
    narrator even when a character is named in the text.
    """
    return line.character_name if line.kind == "dialogue" else None


def speech_clips(
    lines: Sequence[AttributedLine], durations_ms: Sequence[int], scene_ordinal: int
) -> list[SpeechClip]:
    """Pair spoken lines with their durations, grouping them into speech blocks.

    A new block starts whenever the speaker or the narration/dialogue role
    changes, which is what the gap table keys off. ``durations_ms`` are measured
    from TTS output in the render path and estimated from word count in the
    estimator; the grouping is identical either way.
    """
    clips: list[SpeechClip] = []
    block_id = -1
    prev_speaker: str | None = None
    prev_kind: str | None = None
    for line, duration_ms in zip(lines, durations_ms, strict=True):
        speaker = speaker_of(line)
        if block_id < 0 or speaker != prev_speaker or line.kind != prev_kind:
            block_id += 1
        prev_speaker, prev_kind = speaker, line.kind
        clips.append(
            SpeechClip(
                line_ordinal=line.ordinal,
                character_name=speaker,
                duration_ms=duration_ms,
                beat_index=0,
                scene_ordinal=scene_ordinal,
                block_id=block_id,
            )
        )
    return clips
