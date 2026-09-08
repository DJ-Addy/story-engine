"""AI judge / evaluation layer for Story Engine.

Deterministic, offline evaluation of IR-derived material against the story's
needs. Everything here returns a structured, actionable result (score or
proposal + rationale + concrete findings + suggestions), never just a number:

* ``app.judge.voices``   — voice-fit judge (casting vs. character needs).
* ``app.judge.casting``  — casting proposer: the voice *and* the delivery tone
  each part should get, decided from the script before any credit is spent.
* ``app.judge.animatic`` — previz shot-list / animatic quality judge.

Each module computes a deterministic heuristic over the IR, reusing what the
project already ships rather than reinventing it (the animatic judge leans on
the continuity + coverage checks; the proposer on the voice judge's own signal
extraction). Both judges expose a clean, optional seam for a richer
rubric-based evaluation by an injected ``LLMProvider`` (see ``app.adapters``);
the LLM is never required — it defaults to ``None`` and the heuristic is what
the tests exercise, keeping the feature fully offline and deterministic.
"""

from app.judge.animatic import judge_animatic
from app.judge.casting import propose_casting_with_tone
from app.judge.model import (
    AnimaticFinding,
    AnimaticJudgment,
    CastingProposal,
    CastingProposalEntry,
    CharacterVoiceFit,
    RankedEntry,
    RankingResult,
    SceneAnimaticScore,
    VoiceFinding,
    VoiceFitResult,
    VoiceSuggestion,
)
from app.judge.ranking import (
    AnimaticCandidate,
    VoiceCandidate,
    rank_animatics,
    rank_voice_fits,
)
from app.judge.voices import judge_voice_fit

__all__ = [
    "AnimaticCandidate",
    "AnimaticFinding",
    "AnimaticJudgment",
    "CastingProposal",
    "CastingProposalEntry",
    "CharacterVoiceFit",
    "RankedEntry",
    "RankingResult",
    "SceneAnimaticScore",
    "VoiceCandidate",
    "VoiceFinding",
    "VoiceFitResult",
    "VoiceSuggestion",
    "judge_animatic",
    "judge_voice_fit",
    "propose_casting_with_tone",
    "rank_animatics",
    "rank_voice_fits",
]
