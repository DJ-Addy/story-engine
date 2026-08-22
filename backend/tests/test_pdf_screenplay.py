"""Tests for the PDF screenplay classifier.

All tests operate on synthetic word boxes (plain dicts) so no real PDF or
pdfplumber is needed. Standard screenplay indents at 72dpi with a 1.0" margin:
slug/action x0=108, dialogue 180, parenthetical 223.2, character 266.4,
transition 432.
"""

import pytest

from app.ingest.elements import ElementKind, RawElement
from app.ingest.pdf_screenplay import (
    calibrate_left_margin,
    classify_line,
    group_words_into_lines,
    parse_pdf_lines,
)

# Absolute x0 positions (points) for the standard layout.
X_ACTION = 108.0
X_DIALOGUE = 180.0
X_PAREN = 223.2
X_CHARACTER = 266.4
X_TRANSITION = 432.0


def make_line_boxes(
    text: str, x0: float, top: float, page: int = 1, shift: float = 0.0
) -> list[dict]:
    """Build one word box per word, spaced left-to-right from x0."""
    boxes = []
    x = x0 + shift
    for word in text.split():
        boxes.append({"text": word, "x0": x, "top": top, "page": page})
        x += 7.2 * (len(word) + 1)
    return boxes


def two_scene_screenplay_boxes(shift: float = 0.0) -> list[dict]:
    """Synthetic two-scene screenplay as word boxes, optionally shifted right."""
    rows = [
        # (text, x0, top, page)
        ("INT. HARBOR OFFICE - NIGHT", X_ACTION, 100, 1),
        ("Rain hammers the windows.", X_ACTION, 120, 1),
        ("MARA (V.O.)", X_CHARACTER, 140, 1),
        ("(quietly)", X_PAREN, 160, 1),
        ("The ships never came back.", X_DIALOGUE, 180, 1),
        ("Not one of them.", X_DIALOGUE, 200, 1),
        ("CUT TO:", X_TRANSITION, 220, 1),
        ("EXT. LIGHTHOUSE - DAY", X_ACTION, 100, 2),
        ("Gulls wheel overhead.", X_ACTION, 120, 2),
        ("KEEPER", X_CHARACTER, 140, 2),
        ("Storm is coming.", X_DIALOGUE, 160, 2),
    ]
    boxes: list[dict] = []
    for text, x0, top, page in rows:
        boxes.extend(make_line_boxes(text, x0, top, page=page, shift=shift))
    return boxes


class TestCalibrateLeftMargin:
    def test_standard_layout_yields_zero_margin(self) -> None:
        boxes = two_scene_screenplay_boxes()
        assert calibrate_left_margin(boxes) == pytest.approx(0.0)

    def test_shifted_layout_yields_shifted_margin(self) -> None:
        boxes = two_scene_screenplay_boxes(shift=18.0)
        assert calibrate_left_margin(boxes) == pytest.approx(18.0)

    def test_modal_calculation_with_noise(self) -> None:
        # Modal x0 must win despite jitter and outliers. Values within half a
        # point of 108 round to the same modal bucket.
        boxes = [
            {"text": "a", "x0": 108.3, "top": 10, "page": 1},
            {"text": "b", "x0": 107.8, "top": 20, "page": 1},
            {"text": "c", "x0": 108.1, "top": 30, "page": 1},
            {"text": "noise", "x0": 300.0, "top": 40, "page": 1},
            {"text": "noise2", "x0": 55.0, "top": 50, "page": 1},
        ]
        assert calibrate_left_margin(boxes) == pytest.approx(0.0)


class TestClassifyLine:
    @pytest.mark.parametrize(
        ("text", "x0", "expected"),
        [
            ("Rain hammers the windows.", X_ACTION, "action"),
            ("INT. HARBOR OFFICE - NIGHT", X_ACTION, "slugline"),
            ("EXT. LIGHTHOUSE - DAY", X_ACTION, "slugline"),
            ("I/E. CAR - DAY", X_ACTION, "slugline"),
            ("INT/EXT. TRAIN - NIGHT", X_ACTION, "slugline"),
            ("int. lowercase office - day", X_ACTION, "slugline"),
            ("INTERIOR DESIGN IS HER PASSION", X_ACTION, "action"),
            ("The ships never came back.", X_DIALOGUE, "dialogue"),
            ("(quietly)", X_PAREN, "parenthetical"),
            ("MARA (V.O.)", X_CHARACTER, "character"),
            ("CUT TO:", X_TRANSITION, "transition"),
        ],
    )
    def test_bands_at_zero_margin(self, text: str, x0: float, expected: str) -> None:
        assert classify_line(text, x0, 0.0) == expected

    def test_outside_all_bands_is_unknown(self) -> None:
        assert classify_line("orphan text", 10.0, 0.0) == "unknown"
        # 4.5" from margin: between character and transition bands.
        assert classify_line("floating", 4.5 * 72, 0.0) == "unknown"

    def test_margin_shift_preserves_classification(self) -> None:
        boxes = two_scene_screenplay_boxes(shift=18.0)
        margin = calibrate_left_margin(boxes)
        assert classify_line("MARA (V.O.)", X_CHARACTER + 18.0, margin) == "character"
        assert classify_line("Some words.", X_DIALOGUE + 18.0, margin) == "dialogue"


class TestGroupWordsIntoLines:
    def test_joins_words_left_to_right(self) -> None:
        boxes = [
            {"text": "windows.", "x0": 250.0, "top": 100, "page": 1},
            {"text": "Rain", "x0": 108.0, "top": 100, "page": 1},
            {"text": "hammers", "x0": 150.0, "top": 100, "page": 1},
            {"text": "the", "x0": 210.0, "top": 100, "page": 1},
        ]
        assert group_words_into_lines(boxes) == [
            ("Rain hammers the windows.", 108.0, 1)
        ]

    def test_two_point_top_tolerance(self) -> None:
        boxes = [
            {"text": "same", "x0": 108.0, "top": 100.0, "page": 1},
            {"text": "line", "x0": 150.0, "top": 101.5, "page": 1},
            {"text": "next", "x0": 108.0, "top": 110.0, "page": 1},
        ]
        lines = group_words_into_lines(boxes)
        assert [t for t, _, _ in lines] == ["same line", "next"]

    def test_pages_kept_separate(self) -> None:
        boxes = [
            {"text": "one", "x0": 108.0, "top": 100.0, "page": 1},
            {"text": "two", "x0": 108.0, "top": 100.0, "page": 2},
        ]
        lines = group_words_into_lines(boxes)
        assert lines == [("one", 108.0, 1), ("two", 108.0, 2)]

    def test_line_x0_is_min_x0(self) -> None:
        boxes = [
            {"text": "b", "x0": 200.0, "top": 50.0, "page": 1},
            {"text": "a", "x0": 180.0, "top": 50.0, "page": 1},
        ]
        assert group_words_into_lines(boxes)[0][1] == 180.0


class TestParsePdfLines:
    def parse_standard(self) -> list[RawElement]:
        boxes = two_scene_screenplay_boxes()
        margin = calibrate_left_margin(boxes)
        lines = group_words_into_lines(boxes)
        return parse_pdf_lines(lines, margin)

    def test_two_scene_element_kinds(self) -> None:
        elements = self.parse_standard()
        assert [e.kind for e in elements] == [
            ElementKind.SLUGLINE,
            ElementKind.ACTION,
            ElementKind.CHARACTER_CUE,
            ElementKind.PARENTHETICAL,
            ElementKind.DIALOGUE,  # two dialogue lines merged
            ElementKind.TRANSITION,
            ElementKind.SLUGLINE,
            ElementKind.ACTION,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
        ]

    def test_cue_extension_parsed(self) -> None:
        elements = self.parse_standard()
        cues = [e for e in elements if e.kind == ElementKind.CHARACTER_CUE]
        assert cues[0].cue_name == "MARA"
        assert cues[0].cue_extension == "V.O."
        assert cues[1].cue_name == "KEEPER"
        assert cues[1].cue_extension is None

    def test_consecutive_dialogue_merged(self) -> None:
        elements = self.parse_standard()
        dialogue = [e for e in elements if e.kind == ElementKind.DIALOGUE]
        assert dialogue[0].text == "The ships never came back. Not one of them."

    def test_pages_recorded(self) -> None:
        elements = self.parse_standard()
        assert elements[0].page == 1
        assert elements[-1].page == 2

    def test_unknown_band_maps_to_unknown(self) -> None:
        elements = parse_pdf_lines([("orphan", 10.0, 1)], 0.0)
        assert elements[0].kind == ElementKind.UNKNOWN

    def test_shifted_margin_full_pipeline(self) -> None:
        boxes = two_scene_screenplay_boxes(shift=18.0)
        margin = calibrate_left_margin(boxes)
        lines = group_words_into_lines(boxes)
        elements = parse_pdf_lines(lines, margin)
        assert [e.kind for e in elements] == [
            e.kind for e in self.parse_standard()
        ]
