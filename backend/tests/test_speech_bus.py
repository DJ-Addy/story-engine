"""Tests for plan_speech_bus: sequential placement with PRD gap table.

Gap table (largest applicable wins):
  within speech block          180 ms
  speaker change (dialogue)    350 ms
  narration <-> dialogue       500 ms
  beat change                  900 ms
  scene change                1600 ms
"""

from app.render.audio.model import SpeechClip
from app.render.audio.timing import plan_speech_bus


def clip(
    ordinal: int,
    *,
    character: str | None = None,
    duration: int = 1000,
    beat: int = 0,
    scene: int = 0,
    block: int = 0,
) -> SpeechClip:
    return SpeechClip(
        line_ordinal=ordinal,
        character_name=character,
        duration_ms=duration,
        beat_index=beat,
        scene_ordinal=scene,
        block_id=block,
    )


def test_empty_list_yields_empty_plan() -> None:
    plan = plan_speech_bus([])
    assert plan.entries == []
    assert plan.total_ms == 0


def test_single_clip_starts_at_zero_total_is_duration() -> None:
    plan = plan_speech_bus([clip(1, character="ALICE", duration=1234)])
    assert len(plan.entries) == 1
    assert plan.entries[0].start_ms == 0
    assert plan.entries[0].clip_index == 0
    assert plan.total_ms == 1234


def test_intra_block_gap_is_180() -> None:
    clips = [
        clip(1, character="ALICE", duration=1000, block=7),
        clip(2, character="ALICE", duration=2000, block=7),
    ]
    plan = plan_speech_bus(clips)
    assert plan.entries[0].start_ms == 0
    assert plan.entries[1].start_ms == 1000 + 180
    assert plan.total_ms == 1180 + 2000


def test_speaker_change_gap_is_350() -> None:
    clips = [
        clip(1, character="ALICE", duration=1000, block=0),
        clip(2, character="BOB", duration=500, block=1),
    ]
    plan = plan_speech_bus(clips)
    assert plan.entries[1].start_ms == 1000 + 350
    assert plan.total_ms == 1350 + 500


def test_narration_to_dialogue_gap_is_500() -> None:
    clips = [
        clip(1, character=None, duration=1000, block=0),
        clip(2, character="ALICE", duration=500, block=1),
    ]
    plan = plan_speech_bus(clips)
    assert plan.entries[1].start_ms == 1000 + 500


def test_dialogue_to_narration_gap_is_500() -> None:
    clips = [
        clip(1, character="ALICE", duration=1000, block=0),
        clip(2, character=None, duration=500, block=1),
    ]
    plan = plan_speech_bus(clips)
    assert plan.entries[1].start_ms == 1000 + 500


def test_beat_change_gap_is_900() -> None:
    clips = [
        clip(1, character="ALICE", duration=1000, beat=0, block=0),
        clip(2, character="ALICE", duration=500, beat=1, block=1),
    ]
    plan = plan_speech_bus(clips)
    assert plan.entries[1].start_ms == 1000 + 900


def test_scene_change_gap_is_1600() -> None:
    clips = [
        clip(1, character="ALICE", duration=1000, scene=0, block=0),
        clip(2, character="ALICE", duration=500, scene=1, block=1),
    ]
    plan = plan_speech_bus(clips)
    assert plan.entries[1].start_ms == 1000 + 1600


def test_speaker_change_plus_beat_change_uses_largest_900() -> None:
    clips = [
        clip(1, character="ALICE", duration=1000, beat=0, block=0),
        clip(2, character="BOB", duration=500, beat=1, block=1),
    ]
    plan = plan_speech_bus(clips)
    assert plan.entries[1].start_ms == 1000 + 900


def test_scene_change_dominates_all_other_boundaries() -> None:
    # Narration -> dialogue, plus beat change, plus scene change: 1600 wins.
    clips = [
        clip(1, character=None, duration=1000, beat=0, scene=0, block=0),
        clip(2, character="BOB", duration=750, beat=1, scene=1, block=1),
    ]
    plan = plan_speech_bus(clips)
    assert plan.entries[1].start_ms == 1000 + 1600
    assert plan.total_ms == 2600 + 750


def test_sequence_accumulates_gaps_and_derives_roles() -> None:
    clips = [
        clip(1, character=None, duration=2000, block=0),  # narrator
        clip(2, character="ALICE", duration=1000, block=1),  # +500
        clip(3, character="ALICE", duration=1000, block=1),  # +180
    ]
    plan = plan_speech_bus(clips)
    starts = [e.start_ms for e in plan.entries]
    assert starts == [0, 2500, 3680]
    assert plan.total_ms == 4680
    assert plan.entries[0].clip.role == "narration"
    assert plan.entries[1].clip.role == "dialogue"
