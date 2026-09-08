"""Casting proposer: pick a voice *and* a delivery tone for every part.

:mod:`app.judge.voices` answers "is this casting any good?" — but it has to be
handed a casting first. This module answers the question that comes before it:
given only the story graph and the voices a provider publishes, *what should
the casting be, and how should each part be read?* It is the "pre-read the
quote, then decide the tone and the voice" step — before a credit is spent on
TTS, the narrator and every speaking character has a voice, a proposed tone
from the canonical 12-emotion vocabulary, and one sentence naming the evidence
in the script that argued for it.

Three properties are what make the output worth trusting:

* **Deterministic.** No LLM, no network, no clock, and no set-iteration order
  leaking into the result: the catalogue is sorted, every tie is broken by an
  explicit key, and the same graph plus the same voices produce the same
  proposal every time. That is what makes it safe to cache, to diff across
  drafts ("what did the rewrite change about how MARA reads?"), and to assert
  on in tests.
* **Nothing is invented.** Every voice id comes out of the catalogue that was
  passed in, and every tone is a member of :data:`app.nlp.emotion.EMOTIONS`.
  A part with no tonal evidence gets ``tone=None`` and ``confidence=0.0``
  rather than a plausible-sounding guess — an honest "the text does not say"
  is worth more to a director than a fabricated "serious", because the honest
  answer is the one that tells them where to go and write a parenthetical.
* **The evidence is named.** ``tone_evidence`` records whether the tone came
  from ingest's parenthetical tags (strong — the writer wrote it down) or from
  the words of the dialogue itself (weak — inferred), and the two tiers have
  different confidence ceilings so an inference can never out-rank a stated
  intention. The rationale repeats it in prose for the human reading the list.

Signal extraction is deliberately *not* re-implemented here: line counts, the
emotion distribution and the voice-tag fit all come from
:mod:`app.judge.voices`, so a change to how the judge reads a character changes
how the proposer casts one, and a proposal can never be scored against signals
it never saw.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence

from app.adapters.base import Voice
from app.ingest.elements import StoryGraph
from app.judge.model import CastingProposal, CastingProposalEntry

# Intra-package reuse of app.judge.voices' internals, on purpose. The leading
# underscore marks them module-private, not package-private: the proposer and
# the judge have to read a character the *same* way, and re-deriving line
# counts, the emotion distribution or the tag-fit here is exactly the
# duplication that would let the two drift apart over a few refactors.
from app.judge.voices import (
    _CharacterSignals,
    _collect_signals,
    _is_narrator_voice,
    _raw_fit,
    _round,
    _tag_list,
)
from app.nlp.emotion import EMOTIONS, emotion_from_parenthetical
from app.render.audio.timing import SPOKEN_KINDS, speaker_of

# --- tone confidence ceilings ---------------------------------------------- #
# A parenthetical is the writer stating the delivery, so it can reach high
# confidence; a tone read off the words of the line is an inference about
# something nobody wrote down, so it is capped well below — a guess must never
# be able to present itself as strongly as a fact, however many lines agree.
_TAG_CONFIDENCE_CEILING = 0.9
_TEXT_CONFIDENCE_CEILING = 0.5

# Below this share of upper-case letters, a line with an exclamation mark is
# merely emphatic, not a shout. Six letters is the floor for judging case at
# all: "OK!" and "I!" are too short for capitalisation to mean anything.
_SHOUT_CAPS_SHARE = 0.6
_SHOUT_MIN_LETTERS = 6

# Words that turn an exclamation into a demand rather than an outburst. Kept
# tiny and imperative on purpose: a longer list buys a few more hits and a lot
# more false ones, and every entry has to earn a tone nobody wrote down.
_URGENCY_WORDS = frozenset(
    {"come", "go", "hurry", "help", "move", "now", "quick", "quickly", "run", "stop", "wait"}
)

_TONE_SUMMARY_LIMIT = 3  # how many emotions a rationale names before it elides


def _words(text: str) -> set[str]:
    """Lower-cased alphabetic tokens, so a cue matches a word and not a substring."""
    return set("".join(c if c.isalpha() else " " for c in text.lower()).split())


# --------------------------------------------------------------------------- #
# Tone evidence
# --------------------------------------------------------------------------- #
def text_tone_cue(text: str) -> str | None:
    """Read one delivery cue from a spoken line's own words, or ``None``.

    This is the fallback tier, reached only when ingest captured no
    parenthetical for the part. It reads the two things prose actually encodes:

    * **Orthography.** A line typeset in capitals with an exclamation mark is
      the page shouting; ``?!`` is the page being startled. Those are choices a
      writer makes deliberately, which is why they are checked before the words
      are.
    * **Vocabulary**, via :func:`app.nlp.emotion.emotion_from_parenthetical`.
      Reusing the parenthetical lexicon instead of growing a second one keeps a
      single table as the project's definition of "words that name an emotion",
      and its word-level matching means ``scared`` hits where ``scarecrow``
      does not. That table was written for adverbs a writer aims at an actor,
      so pointing it at dialogue does misfire ("It's cold out" reads as
      serious) — which is exactly why this tier is capped at
      ``_TEXT_CONFIDENCE_CEILING`` and says where it came from in the
      rationale.

    Returns ``None`` far more often than not, which is the intended behaviour:
    silence here becomes ``tone=None`` upstream rather than a guess.
    """
    stripped = text.strip()
    if not stripped:
        return None

    letters = [c for c in stripped if c.isalpha()]
    if (
        "!" in stripped
        and len(letters) >= _SHOUT_MIN_LETTERS
        and sum(1 for c in letters if c.isupper()) / len(letters) >= _SHOUT_CAPS_SHARE
    ):
        return "shouting"
    if "?!" in stripped or "!?" in stripped:
        return "surprised"
    if "!" in stripped and _words(stripped) & _URGENCY_WORDS:
        return "urgent"
    return emotion_from_parenthetical(stripped)


def _canonical(emotion: str | None) -> str | None:
    """Drop anything the canonical vocabulary does not own.

    Belt and braces against a hand-edited ``lines.emotion`` or a future lexicon
    entry: the contract is that a proposed tone is an ``EMOTIONS`` member or
    ``None``, and enforcing it at the one place tones are read is cheaper than
    trusting every writer into the field.
    """
    return emotion if emotion in EMOTIONS else None


def _dominant(counter: Counter[str]) -> tuple[str, int]:
    """Argmax with an explicit tie-break: highest count, then alphabetical.

    ``Counter.most_common`` breaks ties by insertion order, which is a property
    of how the graph happened to be walked rather than of the story — two runs
    over equal graphs could then disagree. Alphabetical is arbitrary but stable.
    """
    return min(counter.items(), key=lambda item: (-item[1], item[0]))


def _tone_confidence(ceiling: float, top: int, cued: int, spoken: int) -> float:
    """How far to trust a tone, from how much of the part actually voted for it.

    Three independent doubts, multiplied because any one of them alone should
    be able to sink the number:

    * *dominance* — did the evidence agree, or is this the winner of a 3-way
      split across four tagged lines?
    * *coverage* — how much of the part carried any evidence at all, at half
      weight: one clear parenthetical over a long scene is still a real
      instruction, just not a complete one.
    * *volume* — a single cued line is a sample of one. It reaches full weight
      at four, which is about where a speaking part stops being a walk-on.
    """
    if cued <= 0 or spoken <= 0:
        return 0.0
    dominance = top / cued
    coverage = min(1.0, cued / spoken)
    volume = min(1.0, 0.5 + 0.125 * cued)
    return _round(ceiling * dominance * (0.5 + 0.5 * coverage) * volume)


def _carry(count: int) -> str:
    """Subject-verb agreement, because these sentences are read by people."""
    return "carries" if count == 1 else "carry"


def _summarize(counter: Counter[str]) -> str:
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    head = ", ".join(f"{emotion} x{count}" for emotion, count in ranked[:_TONE_SUMMARY_LIMIT])
    return head + (", ..." if len(ranked) > _TONE_SUMMARY_LIMIT else "")


def _propose_tone(
    signals: _CharacterSignals, texts: Sequence[str]
) -> tuple[str | None, str, float, str]:
    """Pick a tone for one part: ``(tone, evidence, confidence, evidence phrase)``.

    Tags beat text, always — when the writer wrote ``(whispering)`` there is
    nothing left to infer, and mixing an inference into a stated intention can
    only make the answer worse. Only a part with no tags at all falls through
    to reading its own words; a part with neither gets no tone.
    """
    tagged = Counter({e: n for e, n in signals.emotions.items() if _canonical(e)})
    spoken = max(len(texts), sum(tagged.values()))

    if tagged:
        tone, top = _dominant(tagged)
        total_tagged = sum(tagged.values())
        confidence = _tone_confidence(_TAG_CONFIDENCE_CEILING, top, total_tagged, spoken)
        phrase = (
            f"{total_tagged} of them {_carry(total_tagged)} a delivery tag written into "
            f"the script ({_summarize(tagged)})"
        )
        return tone, "emotion_tags", confidence, phrase

    cues: Counter[str] = Counter()
    for text in texts:
        cue = _canonical(text_tone_cue(text))
        if cue is not None:
            cues[cue] += 1
    if cues:
        tone, top = _dominant(cues)
        total_cued = sum(cues.values())
        confidence = _tone_confidence(_TEXT_CONFIDENCE_CEILING, top, total_cued, spoken)
        phrase = (
            f"no line carries a delivery tag, but {total_cued} of them read as "
            f"{_summarize(cues)} from the words themselves"
        )
        return tone, "text_cues", confidence, phrase

    return None, "none", 0.0, "no line carries a delivery tag or any tonal cue in its words"


# --------------------------------------------------------------------------- #
# Graph reading
# --------------------------------------------------------------------------- #
def _narrator_signals(graph: StoryGraph) -> tuple[_CharacterSignals, list[str]]:
    """The narrator's part: every spoken line that no character speaks.

    ``speaker_of`` is the render path's own rule — action and narration are
    read by the narrator even when a character is named in the prose — so the
    part being cast here is exactly the part that will be synthesized, rather
    than a second opinion about what the narrator says.
    """
    signals = _CharacterSignals("the narrator")
    texts: list[str] = []
    for scene in graph.scenes:
        for line in scene.lines:
            if line.kind not in SPOKEN_KINDS or speaker_of(line) is not None:
                continue
            if not line.text.strip():
                continue
            signals.narration_lines += 1
            texts.append(line.text)
            emotion = _canonical(line.emotion)
            if emotion is not None:
                signals.emotions[emotion] += 1
    return signals, texts


def _dialogue_texts(graph: StoryGraph) -> dict[str, list[str]]:
    """Each character's own dialogue, in script order — the fallback tier's corpus."""
    texts: dict[str, list[str]] = {}
    for scene in graph.scenes:
        for line in scene.lines:
            if line.kind != "dialogue" or not line.character_name:
                continue
            if line.text.strip():
                texts.setdefault(line.character_name, []).append(line.text)
    return texts


# --------------------------------------------------------------------------- #
# Voice assignment
# --------------------------------------------------------------------------- #
def _catalogue(voices: Iterable[Voice]) -> list[Voice]:
    """De-duplicate by id, then sort by id.

    Sorting makes the proposal independent of the order a provider happened to
    list its voices in, so a catalogue that comes back shuffled — a dict
    rebuilt, a roster reordered upstream — still casts the same show. Nothing
    is lost by ignoring the caller's order, because voices are chosen here by
    fit and never by position.
    """
    by_id = {voice.id: voice for voice in voices}
    return [by_id[voice_id] for voice_id in sorted(by_id)]


def _best_fit(signals: _CharacterSignals, candidates: Sequence[Voice]) -> Voice:
    """The candidate whose tags best serve this part; ties broken by voice id."""
    return min(candidates, key=lambda voice: (-_raw_fit(signals, voice), voice.id))


def _voice_phrase(voice: Voice) -> str:
    kind = "narrator-tagged voice" if _is_narrator_voice(voice) else "voice"
    return f"{kind} '{voice.name}' ({_tag_list(voice)})"


def _rationale(
    who: str, spoken: int, evidence: str, tone: str | None, voice: Voice, fit: float
) -> str:
    """One sentence: what the script showed, and what was cast off the back of it."""
    if spoken == 0:
        return (
            f"{who} has no spoken lines in this draft, so no tone is proposed and "
            f"{_voice_phrase(voice)} is held for the part (tag fit {fit:.2f})."
        )
    if tone is None:
        return (
            f"{who} speaks {spoken} line(s) and {evidence}, so no tone is proposed and "
            f"{_voice_phrase(voice)} is cast on tag fit alone ({fit:.2f})."
        )
    return (
        f"{who} speaks {spoken} line(s) and {evidence}, so '{tone}' is proposed on "
        f"{_voice_phrase(voice)} (tag fit {fit:.2f})."
    )


def _entry(
    signals: _CharacterSignals,
    voice: Voice,
    texts: Sequence[str],
    *,
    is_narrator: bool,
) -> CastingProposalEntry:
    tone, evidence, confidence, phrase = _propose_tone(signals, texts)
    fit = _round(_raw_fit(signals, voice))
    spoken = signals.narration_lines if is_narrator else signals.dialogue_lines
    who = "The narrator" if is_narrator else signals.name
    return CastingProposalEntry(
        character=None if is_narrator else signals.name,
        is_narrator=is_narrator,
        voice_id=voice.id,
        voice_name=voice.name,
        tone=tone,
        tone_evidence=evidence,
        confidence=confidence,
        voice_fit=fit,
        line_count=spoken,
        rationale=_rationale(who, spoken, phrase, tone, voice, fit),
    )


# --------------------------------------------------------------------------- #
# The proposer
# --------------------------------------------------------------------------- #
def propose_casting_with_tone(graph: StoryGraph, voices: Sequence[Voice]) -> CastingProposal:
    """Propose a voice and a delivery tone for the narrator and every character.

    ``voices`` is the catalogue to cast from — whatever ``list_voices`` on the
    TTS adapter in play returned. Every proposed ``voice_id`` is one of those;
    the function has no way to name a voice the provider does not have.

    Casting order follows the show's own hierarchy. The narrator is cast first
    because it reads every action and narration line and so is the one part
    guaranteed to be heard, and it prefers a ``narrator``-tagged voice. The
    characters follow biggest speaking part first, each taking the best
    remaining fit, so when the catalogue runs short it is the walk-ons that end
    up sharing a voice rather than the leads. Once every voice is spoken for
    the remainder are dealt round-robin: doubling up is a real casting decision
    a small catalogue forces, and it is better made in the open than by
    silently leaving late characters uncast.

    Raises ``ValueError`` on an empty catalogue — there is no honest proposal
    to make without a single voice, and returning an empty cast would only push
    the failure downstream to whoever tried to render it.
    """
    catalogue = _catalogue(voices)
    if not catalogue:
        raise ValueError(
            "cannot propose a casting from an empty voice catalogue: pass at least "
            "one Voice from the TTS provider's list_voices()"
        )

    # The judge's own signal extraction, so the proposer and the judge read a
    # character identically. It seeds every declared character, including ones
    # this draft has not given a line to yet.
    signals = _collect_signals(graph)
    narrator_signals, narrator_texts = _narrator_signals(graph)
    dialogue_texts = _dialogue_texts(graph)

    narrator_pool = [v for v in catalogue if _is_narrator_voice(v)] or catalogue
    narrator_voice = _best_fit(narrator_signals, narrator_pool)
    entries = [_entry(narrator_signals, narrator_voice, narrator_texts, is_narrator=True)]

    # Characters never take the narrator's voice while anything else is free,
    # so narration and dialogue stay tellable apart by ear.
    remaining = [v for v in catalogue if v.id != narrator_voice.id]
    overflow = list(remaining) or [narrator_voice]

    ranked = sorted(signals.values(), key=lambda s: (-s.dialogue_lines, s.name))
    overflow_index = 0
    for character in ranked:
        if remaining:
            voice = _best_fit(character, remaining)
            remaining = [v for v in remaining if v.id != voice.id]
        else:
            voice = overflow[overflow_index % len(overflow)]
            overflow_index += 1
        entries.append(
            _entry(character, voice, dialogue_texts.get(character.name, []), is_narrator=False)
        )

    toned = sum(1 for entry in entries if entry.tone is not None)
    distinct = len({entry.voice_id for entry in entries})
    rationale = (
        f"Cast {len(entries)} part(s) — the narrator and {len(ranked)} character(s) — "
        f"across {distinct} of {len(catalogue)} available voice(s); {toned} part(s) carry a "
        f"tone proposed from evidence in the script, {len(entries) - toned} are left "
        "deliberately untoned for want of any."
    )
    return CastingProposal(entries=entries, rationale=rationale)
