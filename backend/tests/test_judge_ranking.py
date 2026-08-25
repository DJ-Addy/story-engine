"""Tests for the deterministic judge rankers (app/judge/ranking.py).

A clearly-better variant wins; the leaderboard is stable and ties break by label
so results never depend on submission order; empty and duplicate-label candidate
sets are rejected with a clear error. Ranking reuses the single-shot judges, so
the per-entry results are exactly what judge_voice_fit / judge_animatic produce.
"""

from __future__ import annotations

import pytest

from app.adapters.base import Voice
from app.ingest.elements import AttributedLine, NormalizedCharacter, NormalizedScene, StoryGraph
from app.judge.ranking import (
    AnimaticCandidate,
    VoiceCandidate,
    rank_animatics,
    rank_voice_fits,
)
from app.shotlist.schema import SceneShotList, ShotSpec

SOFT = Voice(id="soft1", name="Bella", tags=["female", "conversational", "soft", "american"])
STRONG = Voice(id="strong1", name="Domi", tags=["female", "expressive", "strong", "american"])
NARR = Voice(id="narr1", name="Guy", tags=["male", "narrator", "en-US"])
POOL = [SOFT, STRONG, NARR]


def _line(ordinal: int, kind: str, text: str, name: str | None, emotion: str | None = None):
    return AttributedLine(
        ordinal=ordinal, kind=kind, text=text, character_name=name, emotion=emotion
    )


def two_character_graph() -> StoryGraph:
    """BRUTE: 5 hot lines (angry/shouting/urgent). MARA: 4 subdued lines."""
    brute_emos = ["angry", "angry", "shouting", "angry", "urgent"]
    mara_emos = ["calm", "sad", "calm", "serious"]
    lines: list[AttributedLine] = []
    ordinal = 1
    for emo in brute_emos:
        lines.append(_line(ordinal, "dialogue", "Get out of my way!", "BRUTE", emo))
        ordinal += 1
    for emo in mara_emos:
        lines.append(_line(ordinal, "dialogue", "It's all right now.", "MARA", emo))
        ordinal += 1
    scene = NormalizedScene(
        ordinal=1, slugline="INT. HALL - NIGHT", interior=True, location="HALL",
        time_of_day="NIGHT", lines=lines,
    )
    return StoryGraph(
        scenes=[scene],
        characters=[
            NormalizedCharacter(canonical_name="BRUTE", line_count=5),
            NormalizedCharacter(canonical_name="MARA", line_count=4),
        ],
    )


# --- animatic fixtures (mirror tests/test_judge_animatic.py) --------------- #
def dline(ordinal: int, name: str) -> AttributedLine:
    return AttributedLine(ordinal=ordinal, kind="dialogue", text="A line.", character_name=name)


def aline(ordinal: int) -> AttributedLine:
    return AttributedLine(ordinal=ordinal, kind="action", text="Action.")


def _shot(ordinal, covers, *, size="ms", subjects=("ALICE",), axis="a", lens=50,
          movement="static", eyeline="none") -> ShotSpec:
    return ShotSpec(
        ordinal=ordinal, size=size, subjects=list(subjects), axis_side=axis, lens_mm=lens,
        camera_height="eye", movement=movement, eyeline=eyeline, covers_lines=covers,
        intent="coverage",
    )


def good_shotlist(scene_ordinal: int = 1) -> SceneShotList:
    return SceneShotList(
        scene_ordinal=scene_ordinal, action_axis="ALICE-BOB",
        shots=[
            _shot(1, [2], size="ws", subjects=("ALICE", "BOB"), lens=24),
            _shot(2, [3], size="mcu", subjects=("ALICE",), eyeline="left"),
            _shot(3, [4], size="cu", subjects=("BOB",), lens=85, movement="pan", eyeline="right"),
        ],
    )


def bad_shotlist(scene_ordinal: int = 1) -> SceneShotList:
    """Axis cross, all one size, and two dialogue beats uncovered."""
    return SceneShotList(
        scene_ordinal=scene_ordinal, action_axis="ALICE-BOB",
        shots=[
            _shot(1, [2], size="cu", subjects=("ALICE",), axis="a", eyeline="left"),
            _shot(2, [2], size="cu", subjects=("ALICE",), axis="b", eyeline="right"),
            _shot(3, [2], size="cu", subjects=("ALICE",), axis="b", eyeline="left"),
        ],
    )


def drama_graph() -> StoryGraph:
    lines = [aline(1), dline(2, "ALICE"), dline(3, "BOB"), dline(4, "ALICE")]
    scene = NormalizedScene(
        ordinal=1, slugline="INT. ROOM - NIGHT", interior=True, location="ROOM",
        time_of_day="NIGHT", lines=lines,
    )
    return StoryGraph(scenes=[scene])


# --------------------------------------------------------------------------- #
# Voice ranking
# --------------------------------------------------------------------------- #
class TestRankVoiceFits:
    async def test_better_casting_wins(self) -> None:
        graph = two_character_graph()
        candidates = [
            VoiceCandidate(label="backwards", casting={"BRUTE": SOFT, "MARA": STRONG}),
            VoiceCandidate(label="on-point", casting={"BRUTE": STRONG, "MARA": SOFT}),
        ]
        result = await rank_voice_fits(graph, candidates, available_voices=POOL)

        assert result.winner == "on-point"
        assert [e.label for e in result.entries] == ["on-point", "backwards"]
        assert [e.rank for e in result.entries] == [1, 2]
        # Scores are descending, and each entry surfaces its full judge result.
        assert result.entries[0].overall_score > result.entries[1].overall_score
        assert result.entries[0].overall_score == result.entries[0].result.overall_score
        assert {c.character for c in result.entries[0].result.characters} == {"BRUTE", "MARA"}

    async def test_ties_break_by_label_regardless_of_order(self) -> None:
        graph = two_character_graph()
        casting = {"BRUTE": STRONG, "MARA": SOFT}
        # Identical castings score identically; submit out of label order.
        candidates = [
            VoiceCandidate(label="zeta", casting=dict(casting)),
            VoiceCandidate(label="alpha", casting=dict(casting)),
        ]
        result = await rank_voice_fits(graph, candidates, available_voices=POOL)

        assert result.entries[0].overall_score == result.entries[1].overall_score
        assert [e.label for e in result.entries] == ["alpha", "zeta"]  # tie -> label asc
        assert result.winner == "alpha"
        assert [e.rank for e in result.entries] == [1, 2]

    async def test_empty_candidates_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one candidate"):
            await rank_voice_fits(two_character_graph(), [], available_voices=POOL)

    async def test_duplicate_labels_raise(self) -> None:
        graph = two_character_graph()
        candidates = [
            VoiceCandidate(label="dup", casting={"BRUTE": STRONG}),
            VoiceCandidate(label="dup", casting={"BRUTE": SOFT}),
        ]
        with pytest.raises(ValueError, match="unique"):
            await rank_voice_fits(graph, candidates, available_voices=POOL)


# --------------------------------------------------------------------------- #
# Animatic ranking
# --------------------------------------------------------------------------- #
class TestRankAnimatics:
    def test_better_animatic_wins(self) -> None:
        graph = drama_graph()
        candidates = [
            AnimaticCandidate(label="rough", shotlists=[bad_shotlist(1)]),
            AnimaticCandidate(label="polished", shotlists=[good_shotlist(1)]),
        ]
        result = rank_animatics(graph, candidates)

        assert result.winner == "polished"
        assert [e.label for e in result.entries] == ["polished", "rough"]
        assert [e.rank for e in result.entries] == [1, 2]
        assert result.entries[0].overall_score > result.entries[1].overall_score
        # Full AnimaticJudgment is carried through, per-scene detail included.
        assert result.entries[0].result.scenes[0].scene_ordinal == 1

    def test_ties_break_by_label_regardless_of_order(self) -> None:
        graph = drama_graph()
        candidates = [
            AnimaticCandidate(label="b-cut", shotlists=[good_shotlist(1)]),
            AnimaticCandidate(label="a-cut", shotlists=[good_shotlist(1)]),
        ]
        result = rank_animatics(graph, candidates)

        assert result.entries[0].overall_score == result.entries[1].overall_score
        assert [e.label for e in result.entries] == ["a-cut", "b-cut"]
        assert result.winner == "a-cut"

    def test_empty_candidates_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one candidate"):
            rank_animatics(drama_graph(), [])

    def test_duplicate_labels_raise(self) -> None:
        graph = drama_graph()
        candidates = [
            AnimaticCandidate(label="same", shotlists=[good_shotlist(1)]),
            AnimaticCandidate(label="same", shotlists=[bad_shotlist(1)]),
        ]
        with pytest.raises(ValueError, match="unique"):
            rank_animatics(graph, candidates)
