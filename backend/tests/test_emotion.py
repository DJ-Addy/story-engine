"""Tests for the parenthetical emotion classifier."""

import pytest

from app.nlp.emotion import EMOTIONS, emotion_from_parenthetical


@pytest.mark.parametrize(
    ("parenthetical", "expected"),
    [
        ("(angrily)", "angry"),
        ("(furious)", "angry"),
        ("(happily)", "happy"),
        ("(with a smile, warmly)", "happy"),
        ("(sadly)", "sad"),
        ("(tearful)", "sad"),
        ("(afraid)", "afraid"),
        ("(nervously)", "afraid"),
        ("(excitedly)", "excited"),
        ("(calmly)", "calm"),
        ("(softly)", "calm"),
        ("(whispering)", "whispering"),
        ("(hushed)", "whispering"),
        ("(shouting)", "shouting"),
        ("(yelling)", "shouting"),
        ("(urgently)", "urgent"),
        ("(sarcastically)", "sarcastic"),
        ("(dryly)", "sarcastic"),
        ("(surprised)", "surprised"),
        ("(sternly)", "serious"),
        ("(coldly)", "serious"),
    ],
)
def test_known_parentheticals_resolve(parenthetical: str, expected: str) -> None:
    assert emotion_from_parenthetical(parenthetical) == expected
    assert expected in EMOTIONS


@pytest.mark.parametrize(
    "parenthetical",
    ["(beat)", "(pause)", "(cont'd)", "(V.O.)", "(O.S.)", "(to Mara)", "(more)", "", "()"],
)
def test_non_emotional_parentheticals_return_none(parenthetical: str) -> None:
    assert emotion_from_parenthetical(parenthetical) is None


def test_word_level_matching_avoids_substring_false_positives() -> None:
    # "scold" contains "cold" but is not the whole word -> no serious emotion.
    assert emotion_from_parenthetical("(scolding the dog)") is None


def test_specific_cue_wins_over_general_tone() -> None:
    # Both "whispering" and "softly" (calm) present; whispering is more audible.
    assert emotion_from_parenthetical("(whispering softly)") == "whispering"


def test_case_and_punctuation_insensitive() -> None:
    assert emotion_from_parenthetical("(ANGRILY!)") == "angry"
    assert emotion_from_parenthetical("angrily") == "angry"


def test_multi_word_cue_phrase() -> None:
    assert emotion_from_parenthetical("(under breath)") == "whispering"


def test_multi_word_cue_tolerates_interposed_words() -> None:
    # "under her breath" / "under his breath" still resolve to whispering.
    assert emotion_from_parenthetical("(under her breath)") == "whispering"
    assert emotion_from_parenthetical("(muttering under his breath)") == "whispering"
