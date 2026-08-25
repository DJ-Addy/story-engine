"""Voice-fit judge: score how well each character's assigned voice fits.

The core is a **deterministic heuristic** over the IR and the voice tags — no
network, no credits, importable offline. For each character it:

1. Derives the character's *needs* from what the IR actually contains — the
   distribution of delivery emotions on their dialogue (the canonical
   12-emotion vocabulary in :mod:`app.nlp.emotion`), how much they speak, and
   whether they narrate.
2. Reads the assigned voice's *tags* (gender/role/timbre/energy — the same
   ``Voice`` model the TTS adapters expose, e.g. ``["female", "soft", ...]``)
   into two continuous axes: arousal/energy and warmth.
3. Compares the two, producing a fit score, a short rationale, concrete
   mismatch findings, and — when the fit is poor — ranked alternative voices
   from the available pool.

A clean seam (:func:`judge_voice_fit`'s optional ``llm`` argument) lets an
injected :class:`~app.adapters.base.LLMProvider` add a richer rubric-based note
and, if it returns one, a blended score nudge. The seam is never required: with
``llm=None`` (the default, and what the tests exercise) the result is the pure
heuristic. LLM output is parsed tolerantly and can only *enrich* the heuristic —
a garbage response leaves the deterministic result untouched.
"""

from __future__ import annotations

import json
import re
from collections import Counter

from app.adapters.base import LLMProvider, Voice
from app.ingest.elements import StoryGraph
from app.judge.model import (
    CharacterVoiceFit,
    VoiceFinding,
    VoiceFitResult,
    VoiceSuggestion,
)
from app.nlp.emotion import EMOTIONS

# --- emotion -> need axes -------------------------------------------------- #
# Arousal/energy: how much vocal push the delivery demands.
_HIGH_AROUSAL = frozenset({"angry", "shouting", "excited", "afraid", "surprised", "urgent"})
_LOW_AROUSAL = frozenset({"calm", "sad", "serious", "whispering"})
# The remainder ({"happy", "sarcastic"}) sits mid-arousal.

# Warmth: how warm/positive the delivery reads.
_WARM = frozenset({"happy", "calm", "excited"})
_COLD = frozenset({"angry", "shouting", "serious", "sarcastic"})
# The remainder is warmth-neutral.

assert _HIGH_AROUSAL <= EMOTIONS and _LOW_AROUSAL <= EMOTIONS
assert _WARM <= EMOTIONS and _COLD <= EMOTIONS

# --- voice tag -> axis lookups --------------------------------------------- #
# Recognized tags map to a value on each axis; the voice's position is the mean
# of the values of the tags it carries. Gender/locale tags (male, female,
# en-US, en-GB, american, ...) are intentionally absent — they don't move
# either axis. An unrecognized-only voice defaults to 0.5 (neutral).
_TAG_AROUSAL: dict[str, float] = {
    "expressive": 0.85,
    "energetic": 0.85,
    "strong": 0.80,
    "styles": 0.75,
    "young": 0.60,
    "conversational": 0.50,
    "neutral": 0.50,
    "deep": 0.45,
    "narrator": 0.45,
    "narration": 0.45,
    "warm": 0.40,
    "soft": 0.25,
    "calm": 0.20,
    "gentle": 0.20,
}
_TAG_WARMTH: dict[str, float] = {
    "warm": 0.90,
    "gentle": 0.85,
    "soft": 0.80,
    "calm": 0.75,
    "young": 0.60,
    "conversational": 0.60,
    "narrator": 0.55,
    "narration": 0.55,
    "deep": 0.50,
    "neutral": 0.50,
    "styles": 0.50,
    "energetic": 0.50,
    "expressive": 0.45,
    "strong": 0.35,
}
_NARRATOR_TAGS = frozenset({"narrator", "narration"})

_DEFAULT_AXIS = 0.5

# Findings fire when a need and the voice's axis diverge past these gates.
_AROUSAL_HIGH_GATE = 0.60
_AROUSAL_LOW_GATE = 0.40
_VOICE_SOFT_GATE = 0.45
_VOICE_LOUD_GATE = 0.70
_WARMTH_MISMATCH_GATE = 0.50
_NARRATION_GATE = 0.50  # share of a character's lines that are narration

# A suggestion is only offered if it beats the current fit by this margin.
_SUGGESTION_MARGIN = 0.08
_MAX_SUGGESTIONS = 3


def _round(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


# --------------------------------------------------------------------------- #
# IR-derived character signals
# --------------------------------------------------------------------------- #
class _CharacterSignals:
    """Signals derived from a character's lines across the whole story."""

    __slots__ = ("dialogue_lines", "emotions", "name", "narration_lines")

    def __init__(self, name: str) -> None:
        self.name = name
        self.dialogue_lines = 0
        self.narration_lines = 0
        self.emotions: Counter[str] = Counter()

    @property
    def total_lines(self) -> int:
        return self.dialogue_lines + self.narration_lines

    @property
    def narration_share(self) -> float:
        return self.narration_lines / self.total_lines if self.total_lines else 0.0

    @property
    def narrates(self) -> bool:
        return self.narration_share >= _NARRATION_GATE or "NARRATOR" in self.name.upper()

    def dominant_emotions(self, top: int = 3) -> list[str]:
        return [emotion for emotion, _ in self.emotions.most_common(top)]

    def need_axes(self) -> tuple[float, float]:
        """Return (arousal_need, warmth_need) from the emotion distribution.

        With no emotional cues the character reads as neutral on both axes."""
        total = sum(self.emotions.values())
        if total == 0:
            return _DEFAULT_AXIS, _DEFAULT_AXIS
        high = sum(self.emotions[e] for e in _HIGH_AROUSAL)
        low = sum(self.emotions[e] for e in _LOW_AROUSAL)
        mid = total - high - low
        arousal = (high + 0.5 * mid) / total
        warm = sum(self.emotions[e] for e in _WARM)
        cold = sum(self.emotions[e] for e in _COLD)
        neutral = total - warm - cold
        warmth = (warm + 0.5 * neutral) / total
        return arousal, warmth

    @property
    def high_arousal_share(self) -> float:
        total = sum(self.emotions.values())
        if total == 0:
            return 0.0
        return sum(self.emotions[e] for e in _HIGH_AROUSAL) / total


def _collect_signals(graph: StoryGraph) -> dict[str, _CharacterSignals]:
    """Walk every scene/line once, bucketing dialogue + emotions per speaker.

    Characters declared in ``graph.characters`` are always represented, even if
    they have zero attributed lines, so casting them can still be judged.
    """
    signals: dict[str, _CharacterSignals] = {
        c.canonical_name: _CharacterSignals(c.canonical_name) for c in graph.characters
    }

    def _bucket(name: str) -> _CharacterSignals:
        return signals.setdefault(name, _CharacterSignals(name))

    for scene in graph.scenes:
        for line in scene.lines:
            name = line.character_name
            if name is None:
                continue
            sig = _bucket(name)
            if line.kind == "narration":
                sig.narration_lines += 1
            elif line.kind == "dialogue":
                sig.dialogue_lines += 1
            else:
                continue
            if line.emotion:
                sig.emotions[line.emotion] += 1
    return signals


# --------------------------------------------------------------------------- #
# Voice-tag interpretation
# --------------------------------------------------------------------------- #
def _axis(tags: list[str], table: dict[str, float]) -> float:
    values = [table[t] for t in (tag.lower() for tag in tags) if t in table]
    return sum(values) / len(values) if values else _DEFAULT_AXIS


def voice_axes(voice: Voice) -> tuple[float, float]:
    """Map a voice's tags to (arousal, warmth). Pure and offline."""
    return _axis(voice.tags, _TAG_AROUSAL), _axis(voice.tags, _TAG_WARMTH)


def _is_narrator_voice(voice: Voice) -> bool:
    return any(tag.lower() in _NARRATOR_TAGS for tag in voice.tags)


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def _raw_fit(sig: _CharacterSignals, voice: Voice) -> float:
    """The un-rounded fit in [0, 1] of ``voice`` for character ``sig``."""
    arousal_need, warmth_need = sig.need_axes()
    v_arousal, v_warmth = voice_axes(voice)
    arousal_fit = 1.0 - abs(arousal_need - v_arousal)
    warmth_fit = 1.0 - abs(warmth_need - v_warmth)
    score = 0.6 * arousal_fit + 0.4 * warmth_fit
    if sig.narrates and not _is_narrator_voice(voice):
        score *= 0.6  # a narrator on a non-narration voice is a real miscast
    return max(0.0, min(1.0, score))


def _pct(value: float) -> int:
    return round(value * 100)


def _findings_for(sig: _CharacterSignals, voice: Voice) -> list[VoiceFinding]:
    findings: list[VoiceFinding] = []
    arousal_need, warmth_need = sig.need_axes()
    v_arousal, v_warmth = voice_axes(voice)

    if arousal_need >= _AROUSAL_HIGH_GATE and v_arousal <= _VOICE_SOFT_GATE:
        findings.append(
            VoiceFinding(
                code="AROUSAL_TOO_SOFT",
                severity="warn",
                message=(
                    f"{sig.name}'s lines are {_pct(sig.high_arousal_share)}% high-arousal "
                    f"(angry/shouting/afraid/urgent/...), but voice '{voice.name}' reads "
                    f"soft/low-energy ({_tag_list(voice)}). Consider a more "
                    "expressive/energetic voice."
                ),
            )
        )
    elif arousal_need <= _AROUSAL_LOW_GATE and v_arousal >= _VOICE_LOUD_GATE:
        findings.append(
            VoiceFinding(
                code="AROUSAL_TOO_HOT",
                severity="warn",
                message=(
                    f"{sig.name} is largely calm/subdued, but voice '{voice.name}' reads "
                    f"high-energy ({_tag_list(voice)}). Consider a calmer voice."
                ),
            )
        )

    if abs(warmth_need - v_warmth) >= _WARMTH_MISMATCH_GATE:
        hotter = "warmer" if warmth_need > v_warmth else "cooler"
        findings.append(
            VoiceFinding(
                code="WARMTH_MISMATCH",
                severity="info",
                message=(
                    f"{sig.name}'s delivery reads {hotter} than voice '{voice.name}' "
                    "(warmth axis mismatch)."
                ),
            )
        )

    if sig.narrates and not _is_narrator_voice(voice):
        findings.append(
            VoiceFinding(
                code="NARRATOR_MISMATCH",
                severity="warn",
                message=(
                    f"{sig.name} narrates ({_pct(sig.narration_share)}% of their lines) but "
                    f"voice '{voice.name}' isn't tagged for narration."
                ),
            )
        )
    return findings


def _tag_list(voice: Voice) -> str:
    return ", ".join(voice.tags) if voice.tags else "no tags"


def _suggestions_for(
    sig: _CharacterSignals, assigned: Voice, pool: list[Voice], current: float
) -> list[VoiceSuggestion]:
    ranked = sorted(
        (
            (v, _raw_fit(sig, v))
            for v in pool
            if v.id != assigned.id
        ),
        key=lambda pair: (-pair[1], pair[0].id),
    )
    out: list[VoiceSuggestion] = []
    for voice, fit in ranked:
        if fit < current + _SUGGESTION_MARGIN:
            break
        out.append(VoiceSuggestion(voice_id=voice.id, voice_name=voice.name, score=_round(fit)))
        if len(out) >= _MAX_SUGGESTIONS:
            break
    return out


def _rationale_for(sig: _CharacterSignals, voice: Voice, score: float) -> str:
    band = "strong" if score >= 0.75 else "adequate" if score >= 0.55 else "poor"
    if sig.total_lines == 0:
        speaks = "has no attributed lines in the IR"
    else:
        emotions = sig.dominant_emotions()
        mood = f"mostly {', '.join(emotions)}" if emotions else "emotionally neutral"
        speaks = f"speaks {sig.dialogue_lines} line(s), {mood}"
    return (
        f"{sig.name} {speaks}; voice '{voice.name}' ({_tag_list(voice)}) is a "
        f"{band} fit (score {score:.2f})."
    )


def _score_character(
    sig: _CharacterSignals, voice: Voice, pool: list[Voice], total_lines: int
) -> CharacterVoiceFit:
    raw = _raw_fit(sig, voice)
    score = _round(raw)
    return CharacterVoiceFit(
        character=sig.name,
        voice_id=voice.id,
        voice_name=voice.name,
        score=score,
        rationale=_rationale_for(sig, voice, score),
        line_count=sig.dialogue_lines,
        speaks_share=_round(sig.dialogue_lines / total_lines) if total_lines else 0.0,
        dominant_emotions=sig.dominant_emotions(),
        findings=_findings_for(sig, voice),
        suggestions=_suggestions_for(sig, voice, pool, raw),
    )


def _score_casting(
    graph: StoryGraph, casting: dict[str, Voice], available_voices: list[Voice] | None
) -> VoiceFitResult:
    """The deterministic heuristic. Sync, pure, offline — the tested core."""
    signals = _collect_signals(graph)
    total_dialogue = sum(s.dialogue_lines for s in signals.values())

    # The suggestion pool is the explicit available pool, else the distinct
    # voices used in the casting.
    if available_voices is not None:
        pool = list(available_voices)
    else:
        pool = list({v.id: v for v in casting.values()}.values())

    fits: list[CharacterVoiceFit] = []
    weighted_sum = 0.0
    weight_total = 0.0
    for character in sorted(casting):
        voice = casting[character]
        sig = signals.get(character) or _CharacterSignals(character)
        fit = _score_character(sig, voice, pool, total_dialogue)
        fits.append(fit)
        # Prominent characters (more lines) dominate the overall score; every
        # cast character still carries a floor weight of 1.
        weight = max(1, sig.dialogue_lines)
        weighted_sum += fit.score * weight
        weight_total += weight

    overall = _round(weighted_sum / weight_total) if weight_total else 0.0
    uncast = sorted(
        c.canonical_name
        for c in graph.characters
        if c.canonical_name not in casting and c.line_count > 0
    )
    rationale = (
        f"Judged {len(fits)} cast character(s); overall casting fit {overall:.2f}. "
        + (
            f"{len(uncast)} speaking character(s) have no voice assigned."
            if uncast
            else "Every speaking character is cast."
        )
    )
    return VoiceFitResult(
        overall_score=overall,
        rationale=rationale,
        characters=fits,
        uncast_characters=uncast,
    )


# --------------------------------------------------------------------------- #
# Optional LLM seam
# --------------------------------------------------------------------------- #
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_LLM_SYSTEM = """\
You are a casting director evaluating voice assignments for an audiobook. You \
are given each character's derived needs and the deterministic fit score their \
assigned voice earned. Return ONLY JSON — no prose, no markdown fences — of the \
form:
{
  "overall_note": "<one-sentence overall assessment>",
  "characters": [
    {"character": "<name>", "note": "<one-sentence assessment>",
     "score": <optional float 0..1, your independent fit rating>}
  ]
}
Only include a "score" when you are confident; omit it otherwise."""


def build_voice_rubric_prompt(
    graph: StoryGraph, casting: dict[str, Voice], result: VoiceFitResult
) -> tuple[str, str]:
    """Build the (system, user) prompt pair for the optional LLM refinement."""
    lines = []
    signals = _collect_signals(graph)
    for fit in result.characters:
        sig = signals.get(fit.character)
        arousal, warmth = (sig.need_axes() if sig else (_DEFAULT_AXIS, _DEFAULT_AXIS))
        voice = casting[fit.character]
        lines.append(
            f"- {fit.character}: lines={fit.line_count}, "
            f"emotions={fit.dominant_emotions or ['neutral']}, "
            f"need(arousal={arousal:.2f}, warmth={warmth:.2f}); "
            f"voice='{voice.name}' tags={voice.tags}; heuristic_score={fit.score:.2f}"
        )
    user = "Characters and their assigned voices:\n" + "\n".join(lines)
    return _LLM_SYSTEM, user


def _parse_llm_notes(raw: str) -> dict | None:
    match = _JSON_RE.search(raw or "")
    if match is None:
        return None
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _merge_llm(result: VoiceFitResult, payload: dict) -> VoiceFitResult:
    """Fold tolerant LLM output into the heuristic result (enrich, never break).

    A per-character ``note`` is appended to the rationale; a valid ``score`` is
    blended 50/50 with the heuristic score (bounded to a gentle nudge)."""
    by_name = {c.get("character"): c for c in payload.get("characters", []) if isinstance(c, dict)}
    new_chars: list[CharacterVoiceFit] = []
    for fit in result.characters:
        entry = by_name.get(fit.character)
        updated = fit.model_copy(deep=True)
        if entry:
            note = entry.get("note")
            if isinstance(note, str) and note.strip():
                updated.rationale = f"{updated.rationale} LLM: {note.strip()}"
            llm_score = entry.get("score")
            if isinstance(llm_score, (int, float)) and 0.0 <= llm_score <= 1.0:
                updated.score = _round(0.5 * fit.score + 0.5 * float(llm_score))
        new_chars.append(updated)

    overall_note = payload.get("overall_note")
    rationale = result.rationale
    if isinstance(overall_note, str) and overall_note.strip():
        rationale = f"{rationale} LLM: {overall_note.strip()}"

    # Recompute the overall from any blended scores so the summary stays honest.
    if new_chars:
        overall = _round(sum(c.score for c in new_chars) / len(new_chars))
    else:
        overall = result.overall_score
    return result.model_copy(
        update={"characters": new_chars, "rationale": rationale, "overall_score": overall}
    )


async def judge_voice_fit(
    graph: StoryGraph,
    casting: dict[str, Voice],
    available_voices: list[Voice] | None = None,
    llm: LLMProvider | None = None,
) -> VoiceFitResult:
    """Score a casting against the IR.

    ``casting`` maps a character's canonical name to its assigned :class:`Voice`.
    ``available_voices`` is the pool suggestions are drawn from (defaults to the
    voices already used in the casting). With ``llm=None`` (the default) the
    result is the pure deterministic heuristic; an injected ``llm`` only enriches
    it and can never make the call fail.
    """
    result = _score_casting(graph, casting, available_voices)
    if llm is None:
        return result
    system, user = build_voice_rubric_prompt(graph, casting, result)
    try:
        completion = await llm.complete(system, user, {})
    except Exception:  # noqa: BLE001 -- LLM enrichment is best-effort; the heuristic stands alone
        return result
    payload = _parse_llm_notes(completion.text)
    if payload is None:
        return result
    return _merge_llm(result, payload)
