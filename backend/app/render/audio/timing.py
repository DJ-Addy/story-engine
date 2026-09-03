"""Speech-bus timeline planning from the PRD gap table."""

from __future__ import annotations

from app.render.audio.model import ClipRef, SpeechBusPlan, SpeechClip, TimelineEntry

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
