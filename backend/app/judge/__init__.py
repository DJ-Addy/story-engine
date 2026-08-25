"""AI judge / evaluation layer for Story Engine.

Two deterministic judges that score IR-derived material against the story's
needs and return structured, actionable results (score + rationale + concrete
findings + suggestions), not just a number:

* ``app.judge.voices``   — voice-fit judge (casting vs. character needs).
* ``app.judge.animatic`` — previz shot-list / animatic quality judge.

Both judges compute a deterministic heuristic over the IR (and, for the
animatic judge, reuse the existing continuity + coverage checks rather than
reinventing them). Each exposes a clean, optional seam for a richer
rubric-based evaluation by an injected ``LLMProvider`` (see ``app.adapters``);
the LLM is never required — it defaults to ``None`` and the heuristic is what
the tests exercise, keeping the feature fully offline and deterministic.
"""

from app.judge.animatic import judge_animatic
from app.judge.model import (
    AnimaticFinding,
    AnimaticJudgment,
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
    "rank_animatics",
    "rank_voice_fits",
]
