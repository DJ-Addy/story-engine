import pytest

from app.ingest.elements import ElementKind, RawElement
from app.ingest.fdx import parse_fdx


def kinds(elements: list[RawElement]) -> list[ElementKind]:
    return [e.kind for e in elements]


def para(ptype: str, *texts: str) -> str:
    text_nodes = "".join(f"<Text>{t}</Text>" for t in texts)
    return f'<Paragraph Type="{ptype}">{text_nodes}</Paragraph>'


def build_fdx(*paragraphs: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<FinalDraft DocumentType="Script"><Content>'
        + "".join(paragraphs)
        + "</Content></FinalDraft>"
    )


class TestTypeMapping:
    @pytest.mark.parametrize(
        ("ptype", "expected"),
        [
            ("Scene Heading", ElementKind.SLUGLINE),
            ("Action", ElementKind.ACTION),
            ("Character", ElementKind.CHARACTER_CUE),
            ("Parenthetical", ElementKind.PARENTHETICAL),
            ("Dialogue", ElementKind.DIALOGUE),
            ("Transition", ElementKind.TRANSITION),
        ],
    )
    def test_known_types(self, ptype: str, expected: ElementKind) -> None:
        elements = parse_fdx(build_fdx(para(ptype, "SOME TEXT")))
        assert kinds(elements) == [expected]
        assert elements[0].text == "SOME TEXT"

    def test_unknown_type_maps_to_unknown(self) -> None:
        elements = parse_fdx(build_fdx(para("General", "A production note.")))
        assert kinds(elements) == [ElementKind.UNKNOWN]


class TestTextHandling:
    def test_multiple_text_children_are_concatenated(self) -> None:
        elements = parse_fdx(build_fdx(para("Action", "He opens ", "the door.")))
        assert elements[0].text == "He opens the door."

    def test_paragraph_without_text_is_skipped(self) -> None:
        elements = parse_fdx(build_fdx('<Paragraph Type="Action"></Paragraph>'))
        assert elements == []


class TestCharacterCues:
    def test_plain_cue(self) -> None:
        elements = parse_fdx(build_fdx(para("Character", "MARA")))
        cue = elements[0]
        assert cue.kind == ElementKind.CHARACTER_CUE
        assert cue.cue_name == "MARA"
        assert cue.cue_extension is None

    def test_cue_with_vo_extension(self) -> None:
        elements = parse_fdx(build_fdx(para("Character", "MARA (V.O.)")))
        cue = elements[0]
        assert cue.text == "MARA (V.O.)"
        assert cue.cue_name == "MARA"
        assert cue.cue_extension == "V.O."

    def test_cue_with_contd_extension(self) -> None:
        elements = parse_fdx(build_fdx(para("Character", "TOM (CONT'D)")))
        cue = elements[0]
        assert cue.cue_name == "TOM"
        assert cue.cue_extension == "CONT'D"


class TestSampleFixture:
    def test_exact_element_sequence(self, sample_fdx: str) -> None:
        elements = parse_fdx(sample_fdx)
        assert kinds(elements) == [
            ElementKind.SLUGLINE,
            ElementKind.ACTION,
            ElementKind.CHARACTER_CUE,
            ElementKind.PARENTHETICAL,
            ElementKind.DIALOGUE,
            ElementKind.SLUGLINE,
            ElementKind.ACTION,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.TRANSITION,
            ElementKind.SLUGLINE,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
        ]

    def test_sluglines(self, sample_fdx: str) -> None:
        elements = parse_fdx(sample_fdx)
        sluglines = [e.text for e in elements if e.kind == ElementKind.SLUGLINE]
        assert sluglines == [
            "EXT. HARBOR TOWN - NIGHT",
            "INT. LIGHTHOUSE - KEEPER'S ROOM - NIGHT",
            "EXT. LIGHTHOUSE - CLIFF PATH - NIGHT",
        ]

    def test_cue_names_and_extensions(self, sample_fdx: str) -> None:
        elements = parse_fdx(sample_fdx)
        cues = [
            (e.cue_name, e.cue_extension)
            for e in elements
            if e.kind == ElementKind.CHARACTER_CUE
        ]
        assert cues == [
            ("MARA", None),
            ("TOM", None),
            ("MARA", None),
            ("TOM", "CONT'D"),
            ("MARA", "V.O."),
        ]
