"""Judge ranking: score several candidate variants and rank them ("pick the best").

The automated-judge tier that will later feed a human-voting / community layer.
A caller submits a handful of candidates and gets a leaderboard back — each
candidate is scored by the same deterministic judge the single-shot endpoints
use, then sorted best-first.

Both rankers are thin orchestration over the existing judges:

* :func:`rank_voice_fits` runs :func:`app.judge.voices.judge_voice_fit` on each
  casting variant.
* :func:`rank_animatics` runs :func:`app.judge.animatic.judge_animatic` on each
  set of scene shot lists — the judge already takes shot lists as an argument,
  so ranking whole animatic variants needs no new representation.

Ranking is deterministic: candidates sort by overall score descending, ties
broken by label ascending, so the leaderboard is stable across runs. Labels
must be unique — a duplicate label is a caller error, not something to rank.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import TypeVar

from pydantic import BaseModel, Field

from app.adapters.base import LLMProvider, Voice
from app.ingest.elements import StoryGraph
from app.judge.animatic import judge_animatic
from app.judge.model import (
    AnimaticJudgment,
    RankedEntry,
    RankingResult,
    VoiceFitResult,
)
from app.judge.voices import judge_voice_fit
from app.shotlist.schema import SceneShotList

_ResultT = TypeVar("_ResultT", VoiceFitResult, AnimaticJudgment)


# --------------------------------------------------------------------------- #
# Candidate inputs
# --------------------------------------------------------------------------- #
class VoiceCandidate(BaseModel):
    """One casting variant to rank: a label plus a character -> voice map."""

    label: str = Field(min_length=1)
    casting: dict[str, Voice] = Field(min_length=1)


class AnimaticCandidate(BaseModel):
    """One animatic variant to rank: a label plus its set of scene shot lists."""

    label: str = Field(min_length=1)
    shotlists: list[SceneShotList] = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Leaderboard assembly
# --------------------------------------------------------------------------- #
def _validate_labels(labels: Sequence[str]) -> None:
    """Reject empty candidate sets and duplicate labels with a clear message."""
    if not labels:
        raise ValueError("at least one candidate is required to rank")
    duplicates = sorted({label for label, count in Counter(labels).items() if count > 1})
    if duplicates:
        raise ValueError(
            f"candidate labels must be unique; duplicated: {', '.join(duplicates)}"
        )


def _leaderboard(scored: list[tuple[str, _ResultT]]) -> RankingResult[_ResultT]:
    """Sort ``(label, result)`` pairs into a stable, best-first leaderboard.

    Descending overall score, ties broken by label ascending so the order is
    deterministic regardless of the input order.
    """
    ordered = sorted(scored, key=lambda pair: (-pair[1].overall_score, pair[0]))
    entries = [
        RankedEntry(
            label=label,
            rank=index + 1,
            overall_score=result.overall_score,
            result=result,
        )
        for index, (label, result) in enumerate(ordered)
    ]
    return RankingResult(
        winner=entries[0].label if entries else None,
        entries=entries,
    )


# --------------------------------------------------------------------------- #
# Rankers
# --------------------------------------------------------------------------- #
async def rank_voice_fits(
    graph: StoryGraph,
    candidates: Sequence[VoiceCandidate],
    available_voices: list[Voice] | None = None,
    llm: LLMProvider | None = None,
) -> RankingResult[VoiceFitResult]:
    """Judge each casting variant against the IR and rank them best-first.

    Every candidate is scored with :func:`judge_voice_fit` against the same
    ``graph`` and shared ``available_voices`` pool, so the scores are
    comparable. With ``llm=None`` (the default) the ranking is the pure
    deterministic heuristic. Raises ``ValueError`` for an empty candidate set or
    duplicate labels.
    """
    _validate_labels([c.label for c in candidates])
    scored: list[tuple[str, VoiceFitResult]] = []
    for candidate in candidates:
        result = await judge_voice_fit(
            graph, candidate.casting, available_voices=available_voices, llm=llm
        )
        scored.append((candidate.label, result))
    return _leaderboard(scored)


def rank_animatics(
    graph: StoryGraph,
    candidates: Sequence[AnimaticCandidate],
    grammar_profile: str = "classical",
) -> RankingResult[AnimaticJudgment]:
    """Judge each animatic variant's shot lists against the IR and rank them.

    Each candidate carries its own set of :class:`SceneShotList`s — a whole
    previz variant — scored with :func:`judge_animatic` under the same
    ``grammar_profile``. Raises ``ValueError`` for an empty candidate set or
    duplicate labels.
    """
    _validate_labels([c.label for c in candidates])
    scored: list[tuple[str, AnimaticJudgment]] = [
        (
            candidate.label,
            judge_animatic(graph, candidate.shotlists, grammar_profile=grammar_profile),
        )
        for candidate in candidates
    ]
    return _leaderboard(scored)
