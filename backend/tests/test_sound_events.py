"""Tests for action-text sound-event detection."""

import pytest

from app.nlp.sound_events import detect_sound_events
from app.render.audio.sfx import EVENTS


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MARA hurries past shuttered stalls", ["footsteps"]),
        ("She climbed the last of the spiral stairs", ["footsteps"]),
        ("She pounds on the lighthouse door.", ["knock"]),
        ("A sharp knock at the door.", ["knock"]),
        ("Outside, thunder. He grabs his coat.", ["thunder"]),
        ("a door banged against its frame", ["door_slam"]),
        ("He slammed the door.", ["door_slam"]),
        ("A cold gust cut through the alley.", ["wind_gust"]),
    ],
)
def test_single_events(text: str, expected: list[str]) -> None:
    assert detect_sound_events(text) == expected


def test_multiple_events_in_source_order() -> None:
    # "opens the door" precedes "burst in" -> door_open then wind_gust.
    text = "Tom opens the door. Wind and rain burst in with Mara."
    assert detect_sound_events(text) == ["door_open", "wind_gust"]


def test_no_events() -> None:
    assert detect_sound_events("Then tonight it burns.") == []
    assert detect_sound_events("") == []


def test_no_false_positive_from_unrelated_words() -> None:
    # "Rain hammers the cobblestones" must not read as a door knock.
    assert "knock" not in detect_sound_events("Rain hammers the cobblestones.")
    # "opens" without a door nearby is not a door_open.
    assert detect_sound_events("She opens the letter.") == []


def test_each_event_has_a_synth() -> None:
    for events in [detect_sound_events(t) for t in ("pounds on the door", "thunder", "gust")]:
        for event in events:
            assert event in EVENTS
