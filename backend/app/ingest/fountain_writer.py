"""Emit Fountain text from converted novel scenes.

DELIBERATE DESIGN — round-trip attribution: the emitted Fountain parses back
through ``app.ingest.fountain.parse_fountain`` + ``app.ingest.normalize``
such that every quote becomes a dialogue line attributed to its CHARACTER
cue at confidence 1.0 / source 'cue'. Converted novels enter the pipeline as
first-class screenplays; the novel conversion's *own* attribution confidence
is preserved separately (``NovelConversionResult`` / ``needs_review``), not
encoded in the Fountain text.

Prose paragraphs become wrapped action blocks; quotes become an uppercased
CHARACTER cue ('UNKNOWN SPEAKER' when unattributed) followed by dialogue.
Scene headings reuse the heading hint when it already looks like a slugline,
otherwise a forced '.SCENE {n}' slugline is emitted.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field

from app.ingest.novel import AttributedQuote

_WRAP_WIDTH = 78
_SLUG_PREFIXES = ("INT.", "EXT.", "I/E")
_UNKNOWN_SPEAKER = "UNKNOWN SPEAKER"


@dataclass
class NovelScene:
    """One converted scene: an optional heading hint plus ordered items,
    where each item is either a prose paragraph (str) or an AttributedQuote."""

    heading_hint: str | None = None
    items: list[str | AttributedQuote] = field(default_factory=list)


def novel_to_fountain(
    scenes: list[NovelScene],
    *,
    title: str | None = None,
    author: str | None = None,
) -> str:
    """Render converted novel scenes as a Fountain screenplay."""
    blocks: list[str] = []
    title_page = [f"Title: {title}"] if title else []
    if author:
        title_page.append(f"Author: {author}")
    if title_page:
        blocks.append("\n".join(title_page))

    for number, scene in enumerate(scenes, start=1):
        blocks.append(_heading(scene.heading_hint, number))
        for item in scene.items:
            block = (
                _dialogue_block(item)
                if isinstance(item, AttributedQuote)
                else _action_block(item)
            )
            if block:
                blocks.append(block)

    return "\n\n".join(blocks) + "\n"


def _heading(hint: str | None, number: int) -> str:
    if hint:
        stripped = hint.strip()
        if stripped.upper().startswith(_SLUG_PREFIXES):
            return stripped
        if stripped.startswith(".") and not stripped.startswith(".."):
            return stripped
    return f".SCENE {number}"


def _action_block(text: str) -> str:
    collapsed = " ".join(text.split())
    return textwrap.fill(collapsed, width=_WRAP_WIDTH) if collapsed else ""


def _dialogue_block(quote: AttributedQuote) -> str:
    text = " ".join(quote.text.split())
    if not text:
        return ""
    cue = (quote.speaker or _UNKNOWN_SPEAKER).strip().upper()
    return f"{cue}\n{textwrap.fill(text, width=_WRAP_WIDTH)}"
