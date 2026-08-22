"""Shot-duration estimation for the animatic before audio exists (PRD open question 5)."""

from __future__ import annotations

MIN_LINE_DURATION_MS = 800


def estimate_line_duration_ms(text: str, wpm: int = 150) -> int:
    """Word-count heuristic: words / wpm minutes, floored at 800 ms."""
    words = len(text.split())
    estimate = round(words * 60_000 / wpm)
    return max(estimate, MIN_LINE_DURATION_MS)


def shot_duration_ms(covered_line_texts: list[str], boundary_gaps_ms: int) -> int:
    """Total estimated shot length: per-line estimates plus the gap budget."""
    return sum(estimate_line_duration_ms(text) for text in covered_line_texts) + boundary_gaps_ms
