"""Tests for the deterministic continuity validator."""

from __future__ import annotations

import pytest

from app.continuity.model import Finding, SceneContext, ShotMeta
from app.continuity.profiles import GRAMMAR_PROFILES, active_rules
from app.continuity.validator import validate_scene


def shot(
    ordinal: int,
    size: str = "ms",
    subjects: tuple[str, ...] = (),
    axis: str = "neutral",
    lens: int | None = None,
    eyeline: str | None = None,
) -> ShotMeta:
    return ShotMeta(
        ordinal=ordinal,
        size=size,
        subject_ids=list(subjects),
        axis_side=axis,
        lens_mm=lens,
        eyeline=eyeline,
    )


def scene(
    speakers: tuple[str, ...] = (),
    names: dict[str, str] | None = None,
    tod: str | None = None,
    prev_tod: str | None = None,
) -> SceneContext:
    return SceneContext(
        ordinal=1,
        dialogue_speakers=list(speakers),
        character_names=names or {},
        time_of_day=tod,
        prev_scene_time_of_day=prev_tod,
    )


def codes(findings: list[Finding]) -> list[str]:
    return [f.rule_code for f in findings]


# --- AXIS_CROSS ---


def test_axis_cross_fires_on_a_to_b() -> None:
    findings = validate_scene(scene(), [shot(1, axis="a"), shot(2, axis="b")])
    assert codes(findings) == ["AXIS_CROSS"]
    f = findings[0]
    assert f.severity == "warn"
    assert f.shot_ordinal == 2
    assert "1" in f.message and "2" in f.message


def test_axis_cross_ignores_neutral_between_same_side() -> None:
    shots = [shot(1, axis="a"), shot(2, axis="neutral"), shot(3, axis="a")]
    assert validate_scene(scene(), shots) == []


def test_axis_cross_still_fires_across_neutral_gap() -> None:
    shots = [shot(1, axis="a"), shot(2, axis="neutral"), shot(3, axis="b")]
    assert codes(validate_scene(scene(), shots)) == ["AXIS_CROSS"]


def test_crossing_resets_tracked_side() -> None:
    shots = [shot(1, axis="a"), shot(2, axis="crossing"), shot(3, axis="b")]
    assert validate_scene(scene(), shots) == []


# --- EYELINE_MISMATCH ---


def test_eyeline_mismatch_fires_on_matched_reverse_pair() -> None:
    shots = [
        shot(1, size="cu", subjects=("alice",), eyeline="left"),
        shot(2, size="mcu", subjects=("bob",), eyeline="left"),
    ]
    findings = validate_scene(scene(), shots)
    assert codes(findings) == ["EYELINE_MISMATCH"]
    assert findings[0].severity == "warn"


def test_eyeline_mismatch_clean_when_eyelines_differ() -> None:
    shots = [
        shot(1, size="cu", subjects=("alice",), eyeline="left"),
        shot(2, size="mcu", subjects=("bob",), eyeline="right"),
    ]
    assert validate_scene(scene(), shots) == []


def test_eyeline_mismatch_requires_disjoint_single_subjects() -> None:
    # Same subject in both shots is not a reverse, so no finding.
    shots = [
        shot(1, size="cu", subjects=("alice",), eyeline="left"),
        shot(2, size="cu", subjects=("alice",), eyeline="left"),
    ]
    assert validate_scene(scene(), shots) == []


# --- NO_REVERSE ---


def test_no_reverse_reports_uncovered_speaker_by_display_name() -> None:
    ctx = scene(speakers=("bob",), names={"bob": "Bob Smith"})
    findings = validate_scene(ctx, [shot(1, size="ws", subjects=("bob",))])
    assert codes(findings) == ["NO_REVERSE"]
    f = findings[0]
    assert f.severity == "info"
    assert f.shot_ordinal is None
    assert "Bob Smith" in f.message


def test_no_reverse_clean_when_speaker_has_close_coverage() -> None:
    ctx = scene(speakers=("bob",), names={"bob": "Bob Smith"})
    assert validate_scene(ctx, [shot(1, size="cu", subjects=("bob",))]) == []


# --- LENS_JUMP ---


def test_lens_jump_fires_on_big_gap_same_size() -> None:
    shots = [shot(1, size="ms", lens=24), shot(2, size="ms", lens=135)]
    findings = validate_scene(scene(), shots)
    assert codes(findings) == ["LENS_JUMP"]
    assert findings[0].severity == "info"


def test_lens_jump_clean_when_sizes_differ() -> None:
    shots = [shot(1, size="ms", lens=24), shot(2, size="ws", lens=135)]
    assert validate_scene(scene(), shots) == []


def test_lens_jump_clean_when_gap_small() -> None:
    shots = [shot(1, size="ms", lens=35), shot(2, size="ms", lens=85)]
    assert validate_scene(scene(), shots) == []


def test_lens_jump_clean_when_lens_missing() -> None:
    shots = [shot(1, size="ms", lens=24), shot(2, size="ms", lens=None)]
    assert validate_scene(scene(), shots) == []


# --- SCREEN_DIRECTION_FLIP ---


def test_screen_direction_flip_fires_on_shared_subject() -> None:
    shots = [
        shot(1, subjects=("alice",), eyeline="left"),
        shot(2, subjects=("alice", "bob"), eyeline="right"),
    ]
    findings = validate_scene(scene(), shots)
    assert codes(findings) == ["SCREEN_DIRECTION_FLIP"]
    assert findings[0].severity == "warn"


def test_screen_direction_flip_suppressed_by_crossing() -> None:
    shots = [
        shot(1, subjects=("alice",), eyeline="left"),
        shot(2, subjects=("alice",), eyeline="right", axis="crossing"),
    ]
    assert validate_scene(scene(), shots) == []


def test_screen_direction_flip_clean_without_shared_subject() -> None:
    shots = [
        shot(1, subjects=("alice",), eyeline="left"),
        shot(2, subjects=("bob",), eyeline="right"),
    ]
    assert validate_scene(scene(), shots) == []


# --- TIME_OF_DAY_DRIFT ---


def test_time_of_day_drift_fires_when_changed() -> None:
    findings = validate_scene(scene(tod="night", prev_tod="day"), [])
    assert codes(findings) == ["TIME_OF_DAY_DRIFT"]
    f = findings[0]
    assert f.severity == "info"
    assert f.shot_ordinal is None


def test_time_of_day_drift_clean_when_same_or_unset() -> None:
    assert validate_scene(scene(tod="day", prev_tod="day"), []) == []
    assert validate_scene(scene(tod="day", prev_tod=None), []) == []
    assert validate_scene(scene(tod=None, prev_tod="day"), []) == []


def test_time_of_day_drift_active_in_every_profile() -> None:
    for profile in GRAMMAR_PROFILES:
        findings = validate_scene(scene(tod="night", prev_tod="day"), [], profile=profile)
        assert "TIME_OF_DAY_DRIFT" in codes(findings)


# --- profiles ---


def test_handheld_profile_skips_axis_cross() -> None:
    shots = [shot(1, axis="a"), shot(2, axis="b")]
    assert validate_scene(scene(), shots, profile="handheld") == []


def test_handheld_profile_has_only_time_of_day_drift() -> None:
    assert [r.code for r in active_rules("handheld")] == ["TIME_OF_DAY_DRIFT"]


def test_unknown_profile_rejected() -> None:
    with pytest.raises(ValueError):
        active_rules("noir")


# --- validator ordering ---


def test_findings_sorted_and_deterministic() -> None:
    ctx = scene(speakers=("q",), names={"q": "Quinn"}, tod="night", prev_tod="day")
    # Passed out of ordinal order on purpose; validator must sort shots first.
    shots = [
        shot(3, size="ws", axis="b"),
        shot(1, size="ms", axis="a", lens=24),
        shot(2, size="ms", axis="a", lens=135),
    ]
    findings = validate_scene(ctx, shots)
    assert [(f.rule_code, f.shot_ordinal) for f in findings] == [
        ("LENS_JUMP", 2),
        ("AXIS_CROSS", 3),
        ("NO_REVERSE", None),
        ("TIME_OF_DAY_DRIFT", None),
    ]
    assert findings == validate_scene(ctx, shots)
