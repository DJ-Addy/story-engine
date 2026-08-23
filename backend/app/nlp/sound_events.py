"""Detect discrete sound events in a line's text (for foreground SFX).

Complements ``app.nlp.ambience`` (continuous beds): this reads a screenplay's
action/description text for *events* — a door pounded, thunder, wind bursting
in — and returns them in the order they appear, so the renderer can place a
synthesized SFX (``app.render.audio.sfx``) in sync with the narration.

The table is data, not code. Each event maps to regex patterns; a pattern may
require two words (order-independent) so "opens the door" and "the door opens"
both fire door_open while "opens a window" does not.
"""

from __future__ import annotations

import re

# Event -> list of patterns. A pattern is a tuple of word-regexes that must all
# be present somewhere in the text; the event fires at the earliest match.
_EVENT_PATTERNS: dict[str, list[tuple[str, ...]]] = {
    "knock": [(r"pound(?:s|ed|ing)?",), (r"knock(?:s|ed|ing)?",), (r"rap(?:s|ped|ping)?", r"door")],
    "door_open": [(r"open(?:s|ed|ing)?", r"door")],
    "door_slam": [(r"slam(?:s|med|ming)?",), (r"door", r"bang(?:s|ed|ing)?")],
    "thunder": [(r"thunder\w*",)],
    "wind_gust": [(r"gust\w*",), (r"burst(?:s|ing)?",)],
    "footsteps": [(r"footsteps?",), (r"climb(?:s|ed|ing)?",), (r"stairs?",), (r"hurr(?:y|ies|ied)",)],
}


def _first_match_pos(text: str, pattern: tuple[str, ...]) -> int | None:
    """Earliest position where every word in the pattern occurs, else None."""
    positions: list[int] = []
    for word in pattern:
        m = re.search(rf"\b{word}\b", text)
        if m is None:
            return None
        positions.append(m.start())
    return min(positions)


def detect_sound_events(text: str) -> list[str]:
    """Return the distinct sound events in ``text``, in order of appearance.

    Each event fires at most once per line (a single knock/thunder, not one
    per matching word). Events are ordered by where they first occur so
    "opens the door. Wind ... burst in" yields ['door_open', 'wind_gust'].
    """
    if not text:
        return []
    lowered = text.lower()
    hits: list[tuple[int, str]] = []
    for event, patterns in _EVENT_PATTERNS.items():
        best: int | None = None
        for pattern in patterns:
            pos = _first_match_pos(lowered, pattern)
            if pos is not None:
                best = pos if best is None else min(best, pos)
        if best is not None:
            hits.append((best, event))
    hits.sort()
    return [event for _, event in hits]
