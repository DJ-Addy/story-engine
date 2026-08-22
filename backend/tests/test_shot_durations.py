"""Tests for animatic shot-duration estimation (PRD open question 5)."""

from app.render.audio.durations import estimate_line_duration_ms, shot_duration_ms


def test_five_words_at_default_wpm() -> None:
    # 150 wpm -> 400 ms per word -> 5 words = 2000 ms.
    assert estimate_line_duration_ms("one two three four five") == 2000


def test_short_line_clamps_to_minimum_800ms() -> None:
    # 1 word = 400 ms, below the 800 ms floor.
    assert estimate_line_duration_ms("hello") == 800


def test_empty_text_clamps_to_minimum() -> None:
    assert estimate_line_duration_ms("") == 800


def test_custom_wpm() -> None:
    # 10 words at 300 wpm -> 10/300 * 60000 = 2000 ms.
    text = "a b c d e f g h i j"
    assert estimate_line_duration_ms(text, wpm=300) == 2000


def test_whitespace_splitting_ignores_extra_spaces() -> None:
    assert estimate_line_duration_ms("  one   two  three  ") == estimate_line_duration_ms(
        "one two three"
    )


def test_shot_duration_sums_estimates_plus_gaps() -> None:
    lines = ["one two three four five", "six seven eight nine ten"]
    # 2000 + 2000 + 700 gap budget.
    assert shot_duration_ms(lines, boundary_gaps_ms=700) == 4700


def test_shot_duration_applies_per_line_minimum() -> None:
    # "hi" clamps to 800, second line is 2000.
    assert shot_duration_ms(["hi", "a b c d e"], boundary_gaps_ms=0) == 2800


def test_shot_duration_empty_lines_is_gaps_only() -> None:
    assert shot_duration_ms([], boundary_gaps_ms=500) == 500
