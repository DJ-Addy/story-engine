"""Tests for the deterministic animatic / shot-list judge (app/judge/animatic.py).

Well-covered, continuity-clean scenes score high; scenes with coverage gaps,
axis crosses, monotonous coverage, or unbroken takes score lower and surface the
matching findings. The judge reuses the existing coverage + continuity checks,
so a known AXIS_CROSS is flagged here exactly as the continuity validator flags
it — and disappears under a grammar profile that doesn't register the rule.
"""

from __future__ import annotations

from app.ingest.elements import AttributedLine, NormalizedScene, StoryGraph
from app.judge.animatic import judge_animatic
from app.shotlist.schema import SceneShotList, ShotSpec

_LONG_LINE = " ".join(["word"] * 60)  # ~24s at 150 wpm -> trips the long-take gate


def dline(ordinal: int, name: str, text: str = "A line.") -> AttributedLine:
    return AttributedLine(ordinal=ordinal, kind="dialogue", text=text, character_name=name)


def aline(ordinal: int, text: str = "Action.") -> AttributedLine:
    return AttributedLine(ordinal=ordinal, kind="action", text=text)


def scene(ordinal: int, lines: list[AttributedLine], tod: str | None = "NIGHT") -> NormalizedScene:
    return NormalizedScene(
        ordinal=ordinal, slugline="INT. ROOM - NIGHT", interior=True,
        location="ROOM", time_of_day=tod, lines=lines,
    )


def shot(
    ordinal: int,
    covers: list[int],
    *,
    size: str = "ms",
    subjects: tuple[str, ...] = ("ALICE",),
    axis: str = "a",
    lens: int = 50,
    movement: str = "static",
    eyeline: str = "none",
) -> ShotSpec:
    return ShotSpec(
        ordinal=ordinal, size=size, subjects=list(subjects), axis_side=axis, lens_mm=lens,
        camera_height="eye", movement=movement, eyeline=eyeline, covers_lines=covers,
        intent="coverage",
    )


def shotlist(scene_ordinal: int, shots: list[ShotSpec]) -> SceneShotList:
    return SceneShotList(scene_ordinal=scene_ordinal, action_axis="ALICE-BOB", shots=shots)


# --- reusable fixtures ----------------------------------------------------- #
_DRAMA_LINES = [aline(1), dline(2, "ALICE"), dline(3, "BOB"), dline(4, "ALICE")]


def good_shotlist(scene_ordinal: int = 1) -> SceneShotList:
    """Full coverage, single axis, varied sizes/movement."""
    return shotlist(
        scene_ordinal,
        [
            shot(1, [2], size="ws", subjects=("ALICE", "BOB"), lens=24),
            shot(2, [3], size="mcu", subjects=("ALICE",), eyeline="left"),
            shot(3, [4], size="cu", subjects=("BOB",), lens=85, movement="pan", eyeline="right"),
        ],
    )


def bad_shotlist(scene_ordinal: int = 2) -> SceneShotList:
    """Axis cross, all one size, and two dialogue beats left uncovered."""
    return shotlist(
        scene_ordinal,
        [
            shot(1, [2], size="cu", subjects=("ALICE",), axis="a", eyeline="left"),
            shot(2, [2], size="cu", subjects=("ALICE",), axis="b", eyeline="right"),
            shot(3, [2], size="cu", subjects=("ALICE",), axis="b", eyeline="left"),
        ],
    )


class TestGoodCoverage:
    def test_clean_scene_scores_high(self) -> None:
        graph = StoryGraph(scenes=[scene(1, _DRAMA_LINES)])
        result = judge_animatic(graph, [good_shotlist(1)])
        assert result.overall_score > 0.9
        s = result.scenes[0]
        assert s.coverage_score == 1.0
        assert s.continuity_score == 1.0
        assert not any(f.severity in ("warn", "error") for f in result.findings)


class TestCoverageGap:
    def test_uncovered_beats_are_flagged(self) -> None:
        graph = StoryGraph(scenes=[scene(2, _DRAMA_LINES)])
        result = judge_animatic(graph, [bad_shotlist(2)])
        s = result.scenes[0]
        assert s.coverage_score < 1.0
        gap = next(f for f in s.findings if f.code == "COVERAGE_GAP")
        assert gap.severity == "error"
        assert "3-4" in gap.message


class TestContinuityReuse:
    def test_axis_cross_is_flagged(self) -> None:
        graph = StoryGraph(scenes=[scene(2, _DRAMA_LINES)])
        result = judge_animatic(graph, [bad_shotlist(2)], grammar_profile="classical")
        codes = {f.code for f in result.findings}
        assert "AXIS_CROSS" in codes
        assert result.scenes[0].continuity_score < 0.7

    def test_grammar_profile_suppresses_axis_cross(self) -> None:
        graph = StoryGraph(scenes=[scene(2, _DRAMA_LINES)])
        result = judge_animatic(graph, [bad_shotlist(2)], grammar_profile="handheld")
        assert "AXIS_CROSS" not in {f.code for f in result.findings}
        # Coverage + variety findings are profile-independent and still fire.
        assert "COVERAGE_GAP" in {f.code for f in result.findings}


class TestVariety:
    def test_monotonous_coverage_is_flagged(self) -> None:
        graph = StoryGraph(scenes=[scene(2, _DRAMA_LINES)])
        result = judge_animatic(graph, [bad_shotlist(2)])
        assert "SHOT_MONOTONY" in {f.code for f in result.findings}
        assert result.scenes[0].variety_score < 0.5


class TestPacing:
    def test_long_take_and_undercoverage_flagged(self) -> None:
        lines = [dline(o, "SOLO", _LONG_LINE) for o in range(1, 7)]
        graph = StoryGraph(scenes=[scene(3, lines)])
        one_shot = shotlist(
            3, [shot(1, [1, 2, 3, 4, 5, 6], size="cu", subjects=("SOLO",))]
        )
        result = judge_animatic(graph, [one_shot])
        codes = {f.code for f in result.scenes[0].findings}
        assert "PACING_LONG_TAKE" in codes
        assert "PACING_UNDERCOVERED" in codes
        assert result.scenes[0].pacing_score < 0.7


class TestAggregation:
    def test_overall_blends_scenes_and_ranks_findings(self) -> None:
        graph = StoryGraph(scenes=[scene(1, _DRAMA_LINES), scene(2, _DRAMA_LINES)])
        result = judge_animatic(graph, [good_shotlist(1), bad_shotlist(2)])

        assert [s.scene_ordinal for s in result.scenes] == [1, 2]
        good = next(s for s in result.scenes if s.scene_ordinal == 1)
        bad = next(s for s in result.scenes if s.scene_ordinal == 2)
        assert good.score > bad.score
        assert good.score > result.overall_score > bad.score

        # Findings are ranked error -> warn -> info.
        rank = {"error": 0, "warn": 1, "info": 2}
        severities = [rank[f.severity] for f in result.findings]
        assert severities == sorted(severities)
        assert result.findings[0].severity == "error"

    def test_shotlist_without_matching_scene_is_skipped(self) -> None:
        graph = StoryGraph(scenes=[scene(1, _DRAMA_LINES)])
        result = judge_animatic(graph, [good_shotlist(99)])
        assert result.scenes == []
        assert result.overall_score == 0.0
        assert "nothing to judge" in result.rationale.lower()
