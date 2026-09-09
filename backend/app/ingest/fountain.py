"""Fountain screenplay parser emitting flat ``RawElement`` lists.

Covers the subset of the Fountain spec needed for ingest: title page,
sluglines (prefixed and forced), action, character cues with extensions,
parentheticals, dialogue blocks, transitions, boneyard and notes.
"""

from __future__ import annotations

import re

from app.ingest.elements import ElementKind, RawElement

_TITLE_PAGE_KEYS = {
    "title",
    "credit",
    "author",
    "authors",
    "source",
    "draft date",
    "date",
    "contact",
    "copyright",
    "notes",
}

_SLUG_PREFIXES = ("INT.", "EXT.", "I/E")

_KNOWN_TRANSITIONS = {
    "FADE IN:",
    "FADE OUT.",
    "FADE TO BLACK.",
    "CUT TO BLACK.",
}

_BONEYARD = re.compile(r"/\*.*?\*/", re.DOTALL)
_NOTE = re.compile(r"\[\[.*?\]\]", re.DOTALL)
_CUE_WITH_EXTENSION = re.compile(r"^(?P<name>[^(]+?)\s*\((?P<ext>[^)]+)\)$")


def split_cue(text: str) -> tuple[str, str | None]:
    """Split a character cue into (name, extension), e.g. 'BOB (V.O.)'."""
    match = _CUE_WITH_EXTENSION.match(text.strip())
    if match:
        return match.group("name").strip(), match.group("ext").strip()
    return text.strip(), None


def parse_fountain(text: str) -> list[RawElement]:
    lines = _strip_comments(text).split("\n")
    elements: list[RawElement] = []
    i = _skip_title_page(lines)
    in_dialogue = False
    prev_blank = True

    while i < len(lines):
        stripped = lines[i].strip()
        lineno = i + 1
        if not stripped:
            in_dialogue = False
            prev_blank = True
            i += 1
            continue

        if in_dialogue:
            kind = (
                ElementKind.PARENTHETICAL
                if stripped.startswith("(") and stripped.endswith(")")
                else ElementKind.DIALOGUE
            )
            # A dialogue block runs to the next blank line, so its lines are ONE
            # speech however they happen to be wrapped — that is the Fountain
            # spec, and it is also the difference between a performance and a
            # stutter. Emitting a line per physical line made the audio renderer
            # synthesize each wrapped fragment as its own utterance: a speech
            # broken at column 78 became nine clips, each falling in pitch at a
            # line ending that is not a sentence ending, with a gap after it.
            # `fountain_writer` wraps prose it converts, so every adapted novel
            # arrived pre-broken this way.
            #
            # Rejoining with a space is exact: the wrapper split on whitespace,
            # so nothing but the newline is being undone. A parenthetical ends
            # the run, because the delivery note genuinely divides the speech.
            if (
                kind is ElementKind.DIALOGUE
                and elements
                and elements[-1].kind is ElementKind.DIALOGUE
            ):
                elements[-1].text = f"{elements[-1].text} {stripped}"
            else:
                elements.append(RawElement(kind=kind, text=stripped, source_line=lineno))
        elif stripped.startswith(".") and not stripped.startswith(".."):
            elements.append(
                RawElement(
                    kind=ElementKind.SLUGLINE,
                    text=stripped[1:].strip(),
                    source_line=lineno,
                )
            )
        elif stripped.upper().startswith(_SLUG_PREFIXES):
            elements.append(
                RawElement(kind=ElementKind.SLUGLINE, text=stripped, source_line=lineno)
            )
        elif stripped.startswith(">"):
            elements.append(
                RawElement(
                    kind=ElementKind.TRANSITION,
                    text=stripped[1:].strip(),
                    source_line=lineno,
                )
            )
        elif _is_transition(stripped):
            elements.append(
                RawElement(kind=ElementKind.TRANSITION, text=stripped, source_line=lineno)
            )
        elif prev_blank and _is_cue(stripped) and _next_line_has_content(lines, i):
            name, extension = split_cue(stripped)
            elements.append(
                RawElement(
                    kind=ElementKind.CHARACTER_CUE,
                    text=stripped,
                    cue_name=name,
                    cue_extension=extension,
                    source_line=lineno,
                )
            )
            in_dialogue = True
        elif not prev_blank and elements and elements[-1].kind is ElementKind.ACTION:
            # Action is paragraphs, not lines, for the same reason dialogue is
            # speeches: the block runs to the next blank line. `prev_blank` is
            # the test for "still inside one", so a paragraph the writer wrapped
            # is rejoined and the narrator reads a sentence through instead of
            # stopping wherever column 78 fell.
            elements[-1].text = f"{elements[-1].text} {stripped}"
        else:
            elements.append(
                RawElement(kind=ElementKind.ACTION, text=stripped, source_line=lineno)
            )

        prev_blank = False
        i += 1

    return elements


def _strip_comments(text: str) -> str:
    def keep_newlines(match: re.Match[str]) -> str:
        return "\n" * match.group().count("\n")

    return _NOTE.sub(keep_newlines, _BONEYARD.sub(keep_newlines, text))


def _skip_title_page(lines: list[str]) -> int:
    first = lines[0].strip() if lines else ""
    key, sep, _ = first.partition(":")
    if not sep or key.strip().lower() not in _TITLE_PAGE_KEYS:
        return 0
    i = 0
    while i < len(lines) and lines[i].strip():
        i += 1
    return i


def _is_uppercase(text: str) -> bool:
    return text == text.upper() and any(c.isalpha() for c in text)


def _is_transition(text: str) -> bool:
    return _is_uppercase(text) and (text.endswith("TO:") or text in _KNOWN_TRANSITIONS)


def _is_cue(text: str) -> bool:
    return _is_uppercase(text)


def _next_line_has_content(lines: list[str], i: int) -> bool:
    return i + 1 < len(lines) and bool(lines[i + 1].strip())
