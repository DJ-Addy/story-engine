import pytest

from app.ingest.elements import ElementKind, RawElement
from app.ingest.fountain import parse_fountain


def kinds(elements: list[RawElement]) -> list[ElementKind]:
    return [e.kind for e in elements]


class TestTitlePage:
    def test_title_page_block_is_skipped(self) -> None:
        text = "Title: My Script\nAuthor: Jane Doe\n\nEXT. BEACH - DAY\n\nWaves crash.\n"
        elements = parse_fountain(text)
        assert kinds(elements) == [ElementKind.SLUGLINE, ElementKind.ACTION]
        assert all("My Script" not in e.text for e in elements)

    def test_document_without_title_page(self) -> None:
        elements = parse_fountain("EXT. BEACH - DAY\n\nWaves crash.\n")
        assert kinds(elements) == [ElementKind.SLUGLINE, ElementKind.ACTION]


class TestSluglines:
    @pytest.mark.parametrize(
        "line",
        [
            "INT. OFFICE - DAY",
            "EXT. BEACH - NIGHT",
            "I/E. CAR - DAY",
        ],
    )
    def test_prefixed_sluglines(self, line: str) -> None:
        elements = parse_fountain(f"{line}\n")
        assert kinds(elements) == [ElementKind.SLUGLINE]
        assert elements[0].text == line

    def test_forced_slugline_leading_dot(self) -> None:
        elements = parse_fountain(".BINNACLE ROOM\n")
        assert kinds(elements) == [ElementKind.SLUGLINE]
        assert elements[0].text == "BINNACLE ROOM"

    def test_ellipsis_is_not_a_forced_slugline(self) -> None:
        elements = parse_fountain("...and then, silence.\n")
        assert kinds(elements) == [ElementKind.ACTION]


class TestActionAndCues:
    def test_plain_action_line(self) -> None:
        elements = parse_fountain("Rain hammers the cobblestones.\n")
        assert kinds(elements) == [ElementKind.ACTION]
        assert elements[0].text == "Rain hammers the cobblestones."

    def test_uppercase_line_followed_by_content_is_a_cue(self) -> None:
        elements = parse_fountain("MARA\nHello there.\n")
        assert kinds(elements) == [ElementKind.CHARACTER_CUE, ElementKind.DIALOGUE]
        assert elements[0].cue_name == "MARA"
        assert elements[0].cue_extension is None

    def test_uppercase_line_without_following_content_is_action(self) -> None:
        elements = parse_fountain("MARA\n\nShe waits.\n")
        assert kinds(elements) == [ElementKind.ACTION, ElementKind.ACTION]

    def test_cue_extension_vo(self) -> None:
        elements = parse_fountain("MARA (V.O.)\nHello.\n")
        cue = elements[0]
        assert cue.kind == ElementKind.CHARACTER_CUE
        assert cue.text == "MARA (V.O.)"
        assert cue.cue_name == "MARA"
        assert cue.cue_extension == "V.O."

    def test_cue_extension_contd(self) -> None:
        elements = parse_fountain("TOM (CONT'D)\nGo now.\n")
        cue = elements[0]
        assert cue.kind == ElementKind.CHARACTER_CUE
        assert cue.cue_name == "TOM"
        assert cue.cue_extension == "CONT'D"


class TestDialogueBlocks:
    def test_parenthetical_inside_dialogue_block(self) -> None:
        elements = parse_fountain("MARA\n(whispering)\nCome here.\n")
        assert kinds(elements) == [
            ElementKind.CHARACTER_CUE,
            ElementKind.PARENTHETICAL,
            ElementKind.DIALOGUE,
        ]
        assert elements[1].text == "(whispering)"

    def test_dialogue_block_ends_on_blank_line(self) -> None:
        text = "MARA\nFirst line.\nSecond line.\n\nShe leaves.\n"
        elements = parse_fountain(text)
        assert kinds(elements) == [
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.ACTION,
        ]
        # The blank line is what ends the block — which is this test's point.
        # The two lines before it are ONE speech, not two: see
        # test_wrapped_dialogue_is_one_speech for why that matters.
        assert elements[1].text == "First line. Second line."
        assert elements[2].text == "She leaves."

    def test_wrapped_dialogue_is_one_speech(self) -> None:
        """Lines are how a speech is typed, not how it is delivered.

        A dialogue block runs to the next blank line, so its lines are one
        speech however they were wrapped. Emitting one element per physical
        line made the audio renderer synthesize each fragment separately: a
        speech wrapped at column 78 became nine clips, each falling in pitch
        at a line ending that was not a sentence ending, with a gap after it.
        `fountain_writer` wraps the prose it converts, so every adapted novel
        arrived pre-broken this way.
        """
        text = (
            "MARA\n"
            "My friends, it is not right that one or two of us alone should\n"
            "know the prophecies that Circe has made me. I will therefore\n"
            "tell you about them.\n"
        )
        elements = parse_fountain(text)
        assert kinds(elements) == [ElementKind.CHARACTER_CUE, ElementKind.DIALOGUE]
        assert elements[1].text == (
            "My friends, it is not right that one or two of us alone should "
            "know the prophecies that Circe has made me. I will therefore "
            "tell you about them."
        )

    def test_a_parenthetical_divides_the_speech(self) -> None:
        """A delivery note genuinely splits it, so the join must stop there."""
        text = "MARA\nFirst part.\n(quietly)\nSecond part.\n"
        elements = parse_fountain(text)
        assert kinds(elements) == [
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.PARENTHETICAL,
            ElementKind.DIALOGUE,
        ]
        assert elements[1].text == "First part."
        assert elements[3].text == "Second part."

    def test_wrapped_action_is_one_paragraph(self) -> None:
        """Narration wraps too, and read line by line it stutters the same way."""
        text = (
            "Rain hammers the cobblestones. A lone figure hurries past\n"
            "shuttered stalls toward the lighthouse on the point.\n"
            "\n"
            "A separate paragraph.\n"
        )
        elements = parse_fountain(text)
        assert kinds(elements) == [ElementKind.ACTION, ElementKind.ACTION]
        assert elements[0].text == (
            "Rain hammers the cobblestones. A lone figure hurries past "
            "shuttered stalls toward the lighthouse on the point."
        )
        assert elements[1].text == "A separate paragraph."


class TestTransitions:
    def test_cut_to(self) -> None:
        elements = parse_fountain("CUT TO:\n")
        assert kinds(elements) == [ElementKind.TRANSITION]
        assert elements[0].text == "CUT TO:"

    def test_fade_out(self) -> None:
        elements = parse_fountain("FADE OUT.\n")
        assert kinds(elements) == [ElementKind.TRANSITION]

    def test_forced_transition(self) -> None:
        elements = parse_fountain("> Burn to white.\n")
        assert kinds(elements) == [ElementKind.TRANSITION]
        assert elements[0].text == "Burn to white."


class TestCommentStripping:
    def test_boneyard_spanning_lines_is_stripped(self) -> None:
        text = "/* draft one\nstill omitted */\nEXT. BEACH - DAY\n"
        elements = parse_fountain(text)
        assert kinds(elements) == [ElementKind.SLUGLINE]
        assert elements[0].source_line == 3

    def test_inline_boneyard_is_stripped(self) -> None:
        elements = parse_fountain("She waits. /* secretly */\n")
        assert kinds(elements) == [ElementKind.ACTION]
        assert "secretly" not in elements[0].text

    def test_notes_are_stripped(self) -> None:
        elements = parse_fountain("She waits. [[check pacing]]\n")
        assert kinds(elements) == [ElementKind.ACTION]
        assert elements[0].text == "She waits."


class TestSampleFixture:
    def test_exact_element_sequence(self, sample_fountain: str) -> None:
        elements = parse_fountain(sample_fountain)
        assert kinds(elements) == [
            ElementKind.TRANSITION,
            ElementKind.SLUGLINE,
            ElementKind.ACTION,
            ElementKind.CHARACTER_CUE,
            ElementKind.PARENTHETICAL,
            ElementKind.DIALOGUE,
            ElementKind.ACTION,
            ElementKind.SLUGLINE,
            ElementKind.ACTION,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.CHARACTER_CUE,
            ElementKind.PARENTHETICAL,
            ElementKind.DIALOGUE,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.ACTION,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.TRANSITION,
            ElementKind.SLUGLINE,
            ElementKind.ACTION,
            ElementKind.CHARACTER_CUE,
            ElementKind.DIALOGUE,
            ElementKind.TRANSITION,
        ]

    def test_sluglines(self, sample_fountain: str) -> None:
        elements = parse_fountain(sample_fountain)
        sluglines = [e for e in elements if e.kind == ElementKind.SLUGLINE]
        assert [e.text for e in sluglines] == [
            "EXT. HARBOR TOWN - NIGHT",
            "INT. LIGHTHOUSE - KEEPER'S ROOM - NIGHT",
            "EXT. LIGHTHOUSE - CLIFF PATH - NIGHT",
        ]
        assert sluglines[0].source_line == 7

    def test_cue_names_and_extensions(self, sample_fountain: str) -> None:
        elements = parse_fountain(sample_fountain)
        cues = [
            (e.cue_name, e.cue_extension)
            for e in elements
            if e.kind == ElementKind.CHARACTER_CUE
        ]
        assert cues == [
            ("MARA", None),
            ("TOM", None),
            ("MARA", None),
            ("TOM", None),
            ("MARA", None),
            ("TOM", "CONT'D"),
            ("MARA", "V.O."),
        ]

    def test_title_page_not_emitted(self, sample_fountain: str) -> None:
        elements = parse_fountain(sample_fountain)
        assert all("Lighthouse Wager" not in e.text for e in elements)
        assert all("Test Fixture" not in e.text for e in elements)
