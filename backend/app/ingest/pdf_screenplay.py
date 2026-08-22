"""PDF screenplay classifier (PRD §4.2).

Screenplay PDFs carry no semantic markup, so element kinds are recovered from
horizontal indentation: character cues, parentheticals, dialogue, transitions
and action all sit in well-known indent bands. The core operates on plain
word-box dicts ({"text", "x0", "top", "page"}) so unit tests never touch a
real PDF; ``extract_word_boxes`` is the only pdfplumber-dependent function.

The left margin is calibrated per document rather than hardcoded to 1.0" —
scanned or reformatted scripts shift the whole page, and the PRD calls out
that a fixed margin breaks on them. Action/slug lines are by far the most
common element, so the modal x0 across all word boxes anchors the 1.5"
action indent.
"""

from __future__ import annotations

import re
from collections import Counter

from app.ingest.elements import ElementKind, RawElement
from app.ingest.fountain import split_cue

# (min_inches, max_inches, kind) measured from the calibrated left margin.
# Walked in order; a line belongs to the first band with lo <= x < hi.
INDENT_BANDS: list[tuple[float, float, str]] = [
    (3.5, 4.2, "character"),
    (3.0, 3.5, "parenthetical"),
    (2.3, 3.0, "dialogue"),
    (5.5, 8.0, "transition"),
    (1.2, 2.3, "action_or_slug"),
]

SLUG_RE = re.compile(r"^(INT\.?|EXT\.?|I/E\.?|INT/EXT)[\s.]", re.IGNORECASE)

_POINTS_PER_INCH = 72.0
_ACTION_INDENT_INCHES = 1.5
_TOP_TOLERANCE_PT = 2.0
_INDENT_MERGE_TOLERANCE_PT = 2.0

_CLASSIFICATION_TO_KIND: dict[str, ElementKind] = {
    "slugline": ElementKind.SLUGLINE,
    "action": ElementKind.ACTION,
    "character": ElementKind.CHARACTER_CUE,
    "parenthetical": ElementKind.PARENTHETICAL,
    "dialogue": ElementKind.DIALOGUE,
    "transition": ElementKind.TRANSITION,
    "unknown": ElementKind.UNKNOWN,
}


def calibrate_left_margin(boxes: list[dict]) -> float:
    """Derive the page's left margin from the modal word-box x0.

    The most frequent x0 (rounded to the nearest point) belongs to
    action/slug lines at the standard 1.5" indent, so the page margin is
    modal_x0 - 1.5*72.
    """
    if not boxes:
        return 0.0
    counts = Counter(round(box["x0"]) for box in boxes)
    modal_x0 = counts.most_common(1)[0][0]
    return float(modal_x0) - _ACTION_INDENT_INCHES * _POINTS_PER_INCH


def classify_line(line_text: str, x0: float, page_left_margin: float) -> str:
    """Classify one text line by its indent relative to the calibrated margin."""
    x_inches = (x0 - page_left_margin) / _POINTS_PER_INCH
    for lo, hi, kind in INDENT_BANDS:
        if lo <= x_inches < hi:
            if kind == "action_or_slug":
                return "slugline" if SLUG_RE.match(line_text) else "action"
            return kind
    return "unknown"


def group_words_into_lines(boxes: list[dict]) -> list[tuple[str, float, int]]:
    """Group word boxes into (text, x0, page) lines.

    Words on the same page whose tops are within 2pt are one line, joined
    left-to-right; the line's x0 is the minimum word x0.
    """
    ordered = sorted(boxes, key=lambda b: (b["page"], b["top"], b["x0"]))
    lines: list[tuple[str, float, int]] = []
    current: list[dict] = []

    def flush() -> None:
        if not current:
            return
        words = sorted(current, key=lambda b: b["x0"])
        text = " ".join(w["text"] for w in words)
        lines.append((text, words[0]["x0"], current[0]["page"]))

    for box in ordered:
        if current and (
            box["page"] != current[0]["page"]
            or abs(box["top"] - current[0]["top"]) > _TOP_TOLERANCE_PT
        ):
            flush()
            current = []
        current.append(box)
    flush()
    return lines


def parse_pdf_lines(
    lines: list[tuple[str, float, int]], margin: float
) -> list[RawElement]:
    """Map classified lines to ``RawElement``s.

    Consecutive dialogue lines at the same indent merge into one DIALOGUE
    element. Attribution is left to the normalizer downstream.
    """
    elements: list[RawElement] = []
    prev_dialogue_x0: float | None = None

    for source_line, (text, x0, page) in enumerate(lines, start=1):
        classification = classify_line(text, x0, margin)

        if classification == "dialogue":
            if (
                elements
                and elements[-1].kind == ElementKind.DIALOGUE
                and prev_dialogue_x0 is not None
                and abs(x0 - prev_dialogue_x0) <= _INDENT_MERGE_TOLERANCE_PT
            ):
                elements[-1].text = f"{elements[-1].text} {text}"
                continue
            prev_dialogue_x0 = x0
        else:
            prev_dialogue_x0 = None

        cue_name: str | None = None
        cue_extension: str | None = None
        if classification == "character":
            cue_name, cue_extension = split_cue(text)

        elements.append(
            RawElement(
                kind=_CLASSIFICATION_TO_KIND[classification],
                text=text,
                source_line=source_line,
                page=page,
                cue_name=cue_name,
                cue_extension=cue_extension,
            )
        )
    return elements


def parse_pdf_screenplay(pdf_path: str) -> list[RawElement]:
    """Full pipeline: extract, calibrate, group, classify."""
    boxes = extract_word_boxes(pdf_path)
    margin = calibrate_left_margin(boxes)
    return parse_pdf_lines(group_words_into_lines(boxes), margin)


def extract_word_boxes(pdf_path: str) -> list[dict]:
    """Thin pdfplumber wrapper; excluded from unit tests by design."""
    import pdfplumber

    boxes: list[dict] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            for word in page.extract_words():
                boxes.append(
                    {
                        "text": word["text"],
                        "x0": float(word["x0"]),
                        "top": float(word["top"]),
                        "page": page_number,
                    }
                )
    return boxes
