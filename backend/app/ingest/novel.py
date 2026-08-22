"""Rule-based novel prose -> attributed dialogue extraction (PRD phase 2).

Pipeline stages:

1. ``split_into_scenes`` — chapter headings and scene-break markers.
2. ``extract_segments`` — quote/prose segmentation within a paragraph,
   merging quotes split by 'he said' interruptions into one logical quote.
3. ``attribute_quotes`` — tiered dialogue-tag attribution with confidence
   scoring (PRD ``lines.attribution_confidence`` / ``attribution_source``).
4. ``repair_attributions`` — LLM repair for low-confidence quotes only
   (never the whole book), tolerant of messy LLM output, never raises.
5. ``novel_to_screenplay`` — full chain, emitting Fountain via
   ``app.ingest.fountain_writer`` so converted novels flow through the
   existing screenplay pipeline unchanged.

Attribution tiers, checked in confidence order (confidence / source):

- adjacent name tag  ('"...," said Mara' / 'Mara said, "..."')  0.9 / tag
- action beat        ('Mara set down the lamp. "..."')          0.8 / tag
- resolved pronoun   ('"...," he said' + one known name nearby) 0.7 / tag
- untagged alternation in a two-speaker exchange                0.6 / tag
- LLM repair (below-threshold quotes only)                      0.75 / llm
- unattributed                                                  0.0 / None

An adjacent pronoun tag binds the quote: if it cannot be resolved
unambiguously the quote stays unattributed rather than falling through to
weaker heuristics that might contradict the tag.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from pydantic import BaseModel

from app.adapters.base import LLMProvider, ProviderError

# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class AttributedQuote(BaseModel):
    """A logical quote with its (possibly missing) speaker attribution."""

    text: str
    speaker: str | None = None
    confidence: float = 0.0
    source: str | None = None  # 'tag' | 'llm' (rule tiers all count as 'tag')
    paragraph_index: int = 0  # index into the source paragraph list, for context


class NovelConversionResult(BaseModel):
    fountain_text: str
    scenes: int
    quotes: int
    attributed: int
    needs_review: int  # speaker None or confidence < 0.75
    characters: list[str]


# --------------------------------------------------------------------------
# Scene segmentation
# --------------------------------------------------------------------------

_CHAPTER_RE = re.compile(r"^chapter\s+[\w-]+(?:\s+[\w-]+)?\s*\.?$", re.IGNORECASE)
_ROMAN_RE = re.compile(r"^[IVXLCDM]+\.?$")
_SCENE_BREAK_RE = re.compile(r"^(?:\*(?:\s*\*){2,}|-{3,})$")
_BLANK_RUN_SPLIT = 3


def split_into_scenes(text: str) -> list[str]:
    """Split novel text into scene bodies.

    Chapter headings ('CHAPTER ONE', 'Chapter 12', lone roman numerals) and
    scene-break markers ('***', '* * *', '---', runs of >= 3 blank lines)
    all yield scene boundaries; heading/marker lines are not part of any body.
    """
    return [body for _, body in _segment_scenes(text)]


def _segment_scenes(text: str) -> list[tuple[str | None, str]]:
    """Like split_into_scenes but keeps the chapter heading as a hint."""
    scenes: list[tuple[str | None, str]] = []
    heading: str | None = None
    buf: list[str] = []
    blanks = 0

    def flush(next_heading: str | None) -> None:
        nonlocal heading, buf
        body = "\n".join(buf).strip()
        buf = []
        if body:
            scenes.append((heading, body))
            heading = next_heading
        elif next_heading is not None:
            heading = next_heading

    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            blanks += 1
            if blanks == _BLANK_RUN_SPLIT:
                flush(None)
            else:
                buf.append("")
            continue
        blanks = 0
        if _CHAPTER_RE.match(stripped) or _ROMAN_RE.match(stripped):
            flush(stripped)
        elif _SCENE_BREAK_RE.match(stripped):
            flush(None)
        else:
            buf.append(line)
    flush(None)
    return scenes


def _split_paragraphs(body: str) -> list[str]:
    """Blank-line separated paragraphs with internal whitespace collapsed."""
    return [
        " ".join(chunk.split())
        for chunk in re.split(r"\n\s*\n", body)
        if chunk.strip()
    ]


# --------------------------------------------------------------------------
# Quote extraction
# --------------------------------------------------------------------------

_OPENING_QUOTES = {'"', "\u201c"}
_CLOSING_QUOTES = {'"', "\u201d"}

_TAG_VERBS = (
    r"(?:said|asked|replied|whispered|shouted|muttered|answered|called|cried"
    r"|snapped|added)"
)
_NAME = r"(?:(?:Mr|Mrs|Ms|Dr)\.\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?"
_PRONOUN = r"(?:[Hh]e|[Ss]he|[Tt]hey)"

# Prose between two quote spans that is just a dialogue tag ending in a
# comma, e.g. the 'she said,' in '"Fine," she said, "but hurry."'.
_INTERRUPTION_RE = re.compile(
    rf"^(?:(?:{_NAME}|{_PRONOUN})\s+{_TAG_VERBS}|{_TAG_VERBS}\s+{_NAME})\s*,$"
)


def extract_segments(paragraph: str) -> list[tuple[str, str]]:
    """Split a paragraph into ('quote'|'prose', text) segments.

    Handles straight and curly double quotes. Quotes split by a dialogue-tag
    interruption merge into one logical quote segment; the tag prose is kept
    (after the merged quote) so attribution can still read it.
    """
    raw: list[tuple[str, str]] = []
    kind = "prose"
    buf: list[str] = []

    def flush(k: str) -> None:
        text = "".join(buf).strip()
        buf.clear()
        if text:
            raw.append((k, text))

    for ch in paragraph:
        if kind == "prose" and ch in _OPENING_QUOTES:
            flush("prose")
            kind = "quote"
        elif kind == "quote" and ch in _CLOSING_QUOTES:
            flush("quote")
            kind = "prose"
        else:
            buf.append(ch)
    flush(kind)
    return _merge_interrupted(raw)


def _merge_interrupted(segments: list[tuple[str, str]]) -> list[tuple[str, str]]:
    merged: list[tuple[str, str]] = []
    i = 0
    while i < len(segments):
        kind, text = segments[i]
        if (
            kind == "quote"
            and i + 2 < len(segments)
            and segments[i + 1][0] == "prose"
            and segments[i + 2][0] == "quote"
            and _INTERRUPTION_RE.match(segments[i + 1][1])
        ):
            merged.append(("quote", f"{text} {segments[i + 2][1]}"))
            merged.append(segments[i + 1])
            i += 3
        else:
            merged.append((kind, text))
            i += 1
    return merged


# --------------------------------------------------------------------------
# Character registry helpers
# --------------------------------------------------------------------------

_TITLE_PREFIX_RE = re.compile(r"^(?:Mr|Mrs|Ms|Dr)\.?\s+", re.IGNORECASE)
_POSSESSIVE_RE = re.compile(r"(?:'s|\u2019s)$")
_CAPITALIZED_RE = re.compile(r"[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?")


def _strip_name(token: str) -> str:
    """Canonicalize a name token: drop titles, possessives, edge punctuation."""
    token = token.strip().strip(".,;:!?\"\u201c\u201d")
    token = _TITLE_PREFIX_RE.sub("", token)
    return _POSSESSIVE_RE.sub("", token).strip()


def is_known_character(name: str, characters: Iterable[str]) -> bool:
    """True if ``name`` refers to a registered character.

    Tolerates titles ('Dr. Mara') and possessives ("Mara's").
    """
    stripped = _strip_name(name).casefold()
    return bool(stripped) and any(stripped == c.casefold() for c in characters)


# --------------------------------------------------------------------------
# Dialogue-tag attribution
# --------------------------------------------------------------------------

# Pronoun branches come first so 'He said' is never captured as a name.
_POST_TAG_RE = re.compile(
    rf"^(?:(?P<pron_a>{_PRONOUN})\s+{_TAG_VERBS}"
    rf"|(?P<name_a>{_NAME})\s+{_TAG_VERBS}"
    rf"|{_TAG_VERBS}\s+(?P<name_b>{_NAME}))"
)
_PRE_TAG_RE = re.compile(
    rf"(?:(?P<pron_a>{_PRONOUN})\s+{_TAG_VERBS}"
    rf"|(?P<name_a>{_NAME})\s+{_TAG_VERBS}"
    rf"|{_TAG_VERBS}\s+(?P<name_b>{_NAME}))\s*[,:]$"
)
# A prose segment that is nothing but a dialogue tag (dropped from output).
_PURE_TAG_RE = re.compile(
    rf"^(?:(?:{_NAME}|{_PRONOUN})\s+{_TAG_VERBS}"
    rf"|{_TAG_VERBS}\s+(?:{_NAME}|{_PRONOUN}))\s*[.,!?:]?$"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_LEADING_NAME_RE = re.compile(_NAME)


class _AttributionState:
    """Incremental registry + alternation memory shared across paragraphs."""

    def __init__(self) -> None:
        self.registry: dict[str, str] = {}  # casefolded name -> display name
        self.speaker_history: list[str] = []
        self.prev_para_attributed = False

    # -- registry ----------------------------------------------------------

    def register(self, raw_name: str) -> str:
        display = _strip_name(raw_name)
        return self.registry.setdefault(display.casefold(), display)

    def lookup(self, token: str) -> str | None:
        return self.registry.get(_strip_name(token).casefold())

    def known_names(self) -> list[str]:
        return sorted(self.registry.values())

    # -- paragraph processing ------------------------------------------------

    def process_paragraph(
        self, paragraph: str, paragraph_index: int
    ) -> list[str | AttributedQuote]:
        """Attribute the paragraph's quotes; return ordered scene items.

        Prose segments that are pure dialogue tags ('said Mara.') are
        consumed by attribution and dropped from the returned items.
        """
        segments = extract_segments(paragraph)
        prev_para_attributed = self.prev_para_attributed
        quote_only = bool(segments) and all(kind == "quote" for kind, _ in segments)

        items: list[str | AttributedQuote] = []
        any_attributed = False
        for idx, (kind, text) in enumerate(segments):
            if kind == "prose":
                if not _PURE_TAG_RE.match(text):
                    items.append(text)
                continue
            quote = AttributedQuote(text=text, paragraph_index=paragraph_index)
            self._attribute(quote, segments, idx, quote_only, prev_para_attributed)
            if quote.speaker is not None:
                self.speaker_history.append(quote.speaker)
                any_attributed = True
            items.append(quote)

        self.prev_para_attributed = any_attributed
        return items

    # -- attribution tiers ---------------------------------------------------

    def _attribute(
        self,
        quote: AttributedQuote,
        segments: list[tuple[str, str]],
        idx: int,
        quote_only: bool,
        prev_para_attributed: bool,
    ) -> None:
        name, has_pronoun_tag = self._adjacent_tag(segments, idx)
        if name is not None:
            quote.speaker = self.register(name)
            quote.confidence, quote.source = 0.9, "tag"
            return
        if has_pronoun_tag:
            resolved = self._unambiguous_paragraph_name(segments)
            if resolved is not None:
                quote.speaker, quote.confidence, quote.source = resolved, 0.7, "tag"
            return
        beat = self._action_beat(segments, idx)
        if beat is not None:
            quote.speaker, quote.confidence, quote.source = beat, 0.8, "tag"
            return
        if quote_only and prev_para_attributed:
            alternate = self._alternate_speaker()
            if alternate is not None:
                quote.speaker, quote.confidence, quote.source = alternate, 0.6, "tag"

    def _adjacent_tag(
        self, segments: list[tuple[str, str]], idx: int
    ) -> tuple[str | None, bool]:
        """Look for a dialogue tag right after (post) or before (pre) the quote.

        Returns (proper name if any, whether a pronoun tag was seen).
        """
        pronoun_seen = False
        checks = []
        if idx + 1 < len(segments) and segments[idx + 1][0] == "prose":
            checks.append((_POST_TAG_RE.match, segments[idx + 1][1]))
        if idx > 0 and segments[idx - 1][0] == "prose":
            checks.append((_PRE_TAG_RE.search, segments[idx - 1][1]))
        for matcher, text in checks:
            match = matcher(text)
            if not match:
                continue
            name = match.group("name_a") or match.group("name_b")
            if name:
                return name, pronoun_seen
            pronoun_seen = True
        return None, pronoun_seen

    def _unambiguous_paragraph_name(
        self, segments: list[tuple[str, str]]
    ) -> str | None:
        """Resolve a pronoun tag to the paragraph's single known character."""
        found: list[str] = []
        for kind, text in segments:
            if kind != "prose":
                continue
            for match in _CAPITALIZED_RE.finditer(text):
                for candidate in (match.group(), *match.group().split()):
                    display = self.lookup(candidate)
                    if display is not None:
                        if display not in found:
                            found.append(display)
                        break
        return found[0] if len(found) == 1 else None

    def _action_beat(
        self, segments: list[tuple[str, str]], idx: int
    ) -> str | None:
        """A same-paragraph sentence whose subject is a known character."""
        order = [*range(idx - 1, -1, -1), *range(idx + 1, len(segments))]
        for j in order:
            kind, text = segments[j]
            if kind != "prose":
                continue
            subject = self._beat_subject(text)
            if subject is not None:
                return subject
        return None

    def _beat_subject(self, text: str) -> str | None:
        best: str | None = None
        for sentence in _SENTENCE_SPLIT_RE.split(text):
            match = _LEADING_NAME_RE.match(sentence.strip())
            if not match:
                continue
            for candidate in (match.group(), *match.group().split()):
                display = self.lookup(candidate)
                if display is not None:
                    best = display
                    break
        return best

    def _alternate_speaker(self) -> str | None:
        """In a two-speaker exchange, the speaker who did not talk last."""
        if not self.speaker_history:
            return None
        last = self.speaker_history[-1]
        for speaker in reversed(self.speaker_history):
            if speaker != last:
                return speaker
        return None


def attribute_quotes(paragraphs: Iterable[str]) -> list[AttributedQuote]:
    """Attribute every quote in the given paragraphs (in reading order)."""
    state = _AttributionState()
    quotes: list[AttributedQuote] = []
    for index, paragraph in enumerate(paragraphs):
        for item in state.process_paragraph(paragraph, index):
            if isinstance(item, AttributedQuote):
                quotes.append(item)
    return quotes


# --------------------------------------------------------------------------
# LLM repair pass
# --------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?|\n?```\s*$", re.MULTILINE)

_REPAIR_SYSTEM = (
    "You attribute quoted dialogue from a novel to its speaker. "
    "You receive a JSON payload with the known character list and a batch of "
    "quotes, each with an index and its surrounding context paragraph. "
    'Respond with JSON only, shaped as {"attributions": '
    '[{"index": <int>, "speaker": <string or null>}]}. '
    "Use null when the speaker cannot be determined."
)

_REPAIR_CONFIDENCE = 0.75


async def repair_attributions(
    quotes: list[AttributedQuote],
    context_paragraphs: list[str],
    characters: list[str],
    llm: LLMProvider,
    threshold: float = 0.75,
) -> list[AttributedQuote]:
    """Ask the LLM to attribute quotes below ``threshold`` confidence.

    Only the low-confidence quotes (with their context paragraphs) are sent —
    never the whole book. Successful attributions get confidence 0.75 and
    source 'llm'; anything unparseable or failing leaves quotes unchanged.
    Never raises.
    """
    batch = [(i, q) for i, q in enumerate(quotes) if q.confidence < threshold]
    if not batch:
        return quotes

    payload = {
        "characters": list(characters),
        "quotes": [
            {
                "index": index,
                "text": quote.text,
                "context": (
                    context_paragraphs[quote.paragraph_index]
                    if 0 <= quote.paragraph_index < len(context_paragraphs)
                    else ""
                ),
            }
            for index, quote in batch
        ],
    }

    try:
        result = await llm.complete(_REPAIR_SYSTEM, json.dumps(payload), {})
    except ProviderError:
        return quotes

    attributions = _parse_repair_response(result.text)
    if attributions is None:
        return quotes

    eligible = {index for index, _ in batch}
    for entry in attributions:
        if not isinstance(entry, dict):
            continue
        index = entry.get("index")
        speaker = entry.get("speaker")
        if not isinstance(index, int) or index not in eligible:
            continue
        if not isinstance(speaker, str) or not speaker.strip():
            continue
        quote = quotes[index]
        quote.speaker = speaker.strip()
        quote.confidence = _REPAIR_CONFIDENCE
        quote.source = "llm"
    return quotes


def _parse_repair_response(raw: str) -> list | None:
    """Tolerant parse of the repair JSON (mirrors app.shotlist.repair)."""
    text = _FENCE_RE.sub("", raw).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    attributions = payload.get("attributions")
    return attributions if isinstance(attributions, list) else None


# --------------------------------------------------------------------------
# Top-level conversion
# --------------------------------------------------------------------------


async def novel_to_screenplay(
    text: str,
    llm: LLMProvider | None = None,
    *,
    title: str | None = None,
    author: str | None = None,
) -> NovelConversionResult:
    """Convert novel prose to Fountain, chaining all pipeline stages.

    With ``llm=None`` the LLM repair pass is skipped and low-confidence
    quotes surface in ``needs_review``.
    """
    from app.ingest.fountain_writer import NovelScene, novel_to_fountain

    state = _AttributionState()
    all_paragraphs: list[str] = []
    quotes: list[AttributedQuote] = []
    novel_scenes: list[NovelScene] = []

    for heading, body in _segment_scenes(text):
        items: list[str | AttributedQuote] = []
        for paragraph in _split_paragraphs(body):
            paragraph_index = len(all_paragraphs)
            all_paragraphs.append(paragraph)
            for item in state.process_paragraph(paragraph, paragraph_index):
                items.append(item)
                if isinstance(item, AttributedQuote):
                    quotes.append(item)
        novel_scenes.append(NovelScene(heading_hint=heading, items=items))

    if llm is not None:
        await repair_attributions(quotes, all_paragraphs, state.known_names(), llm)

    names = {quote.speaker for quote in quotes if quote.speaker}
    characters = sorted(set(state.registry.values()) | names)
    return NovelConversionResult(
        fountain_text=novel_to_fountain(novel_scenes, title=title, author=author),
        scenes=len(novel_scenes),
        quotes=len(quotes),
        attributed=sum(1 for quote in quotes if quote.speaker is not None),
        needs_review=sum(
            1
            for quote in quotes
            if quote.speaker is None or quote.confidence < 0.75
        ),
        characters=characters,
    )
