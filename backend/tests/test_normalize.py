import pytest

from app.ingest.elements import ElementKind, RawElement, StoryGraph
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize


def slug(text: str) -> RawElement:
    return RawElement(kind=ElementKind.SLUGLINE, text=text)


def cue(name: str, extension: str | None = None) -> RawElement:
    text = f"{name} ({extension})" if extension else name
    return RawElement(
        kind=ElementKind.CHARACTER_CUE,
        text=text,
        cue_name=name,
        cue_extension=extension,
    )


def dialogue(text: str) -> RawElement:
    return RawElement(kind=ElementKind.DIALOGUE, text=text)


def action(text: str) -> RawElement:
    return RawElement(kind=ElementKind.ACTION, text=text)


def paren(text: str) -> RawElement:
    return RawElement(kind=ElementKind.PARENTHETICAL, text=text)


def transition(text: str) -> RawElement:
    return RawElement(kind=ElementKind.TRANSITION, text=text)


class TestSceneSplitting:
    def test_scenes_split_on_sluglines_with_one_based_ordinals(self) -> None:
        graph = normalize(
            [
                slug("INT. OFFICE - DAY"),
                action("Papers everywhere."),
                slug("EXT. STREET - DAY"),
                action("Traffic roars."),
            ]
        )
        assert [s.ordinal for s in graph.scenes] == [1, 2]
        assert graph.scenes[0].slugline == "INT. OFFICE - DAY"
        assert graph.scenes[1].slugline == "EXT. STREET - DAY"

    def test_elements_before_first_slugline_form_scene_zero(self) -> None:
        graph = normalize(
            [
                transition("FADE IN:"),
                slug("INT. OFFICE - DAY"),
                action("Papers everywhere."),
            ]
        )
        assert [s.ordinal for s in graph.scenes] == [0, 1]
        assert graph.scenes[0].slugline is None
        assert [line.kind for line in graph.scenes[0].lines] == ["transition"]

    def test_empty_preamble_is_omitted(self) -> None:
        graph = normalize([slug("INT. OFFICE - DAY"), action("Quiet.")])
        assert [s.ordinal for s in graph.scenes] == [1]


class TestSluglineParsing:
    @pytest.mark.parametrize(
        ("slugline", "interior", "location", "time_of_day"),
        [
            ("INT. OFFICE - DAY", True, "OFFICE", "DAY"),
            ("EXT. BEACH - NIGHT", False, "BEACH", "NIGHT"),
            ("I/E. CAR - DAY", None, "CAR", "DAY"),
            (
                "INT. LIGHTHOUSE - KEEPER'S ROOM - NIGHT",
                True,
                "LIGHTHOUSE - KEEPER'S ROOM",
                "NIGHT",
            ),
            ("EXT. OPEN SEA", False, "OPEN SEA", None),
        ],
    )
    def test_slugline_fields(
        self,
        slugline: str,
        interior: bool | None,
        location: str,
        time_of_day: str | None,
    ) -> None:
        graph = normalize([slug(slugline)])
        scene = graph.scenes[0]
        assert scene.interior is interior
        assert scene.location == location
        assert scene.time_of_day == time_of_day


class TestAttribution:
    def test_dialogue_attributed_from_preceding_cue(self) -> None:
        graph = normalize(
            [slug("INT. OFFICE - DAY"), cue("MARA"), dialogue("Hello.")]
        )
        line = graph.scenes[0].lines[0]
        assert line.kind == "dialogue"
        assert line.character_name == "MARA"
        assert line.attribution_confidence == 1.0
        assert line.attribution_source == "cue"

    def test_contd_resolves_to_same_character(self) -> None:
        graph = normalize(
            [
                slug("INT. OFFICE - DAY"),
                cue("TOM"),
                dialogue("First speech."),
                action("He paces."),
                cue("TOM", "CONT'D"),
                dialogue("Second speech."),
            ]
        )
        lines = graph.scenes[0].lines
        speakers = [line.character_name for line in lines if line.kind == "dialogue"]
        assert speakers == ["TOM", "TOM"]
        assert len(graph.characters) == 1
        assert graph.characters[0].canonical_name == "TOM"
        assert graph.characters[0].line_count == 2

    def test_vo_resolves_to_same_character(self) -> None:
        graph = normalize(
            [
                slug("EXT. SEA - NIGHT"),
                cue("MARA"),
                dialogue("On screen."),
                cue("MARA", "V.O."),
                dialogue("In voice-over."),
            ]
        )
        assert len(graph.characters) == 1
        assert graph.characters[0].canonical_name == "MARA"
        assert graph.characters[0].line_count == 2

    def test_action_lines_have_no_character(self) -> None:
        graph = normalize([slug("INT. OFFICE - DAY"), action("Wind howls.")])
        line = graph.scenes[0].lines[0]
        assert line.kind == "action"
        assert line.character_name is None
        assert line.attribution_confidence is None
        assert line.attribution_source is None

    def test_parenthetical_attaches_to_speaking_character(self) -> None:
        graph = normalize(
            [
                slug("INT. OFFICE - DAY"),
                cue("MARA"),
                paren("(whispering)"),
                dialogue("Come here."),
            ]
        )
        line = graph.scenes[0].lines[0]
        assert line.kind == "parenthetical"
        assert line.character_name == "MARA"

    def test_action_resets_speaker(self) -> None:
        graph = normalize(
            [
                slug("INT. OFFICE - DAY"),
                cue("MARA"),
                dialogue("Hello."),
                action("She turns away."),
            ]
        )
        action_line = graph.scenes[0].lines[1]
        assert action_line.character_name is None


class TestCharacters:
    def test_characters_deduped_with_dialogue_only_line_counts(self) -> None:
        graph = normalize(
            [
                slug("INT. OFFICE - DAY"),
                cue("MARA"),
                paren("(sharp)"),
                dialogue("One."),
                cue("TOM"),
                dialogue("Two."),
                cue("MARA", "CONT'D"),
                dialogue("Three."),
                dialogue("Four."),
            ]
        )
        by_name = {c.canonical_name: c.line_count for c in graph.characters}
        assert by_name == {"MARA": 3, "TOM": 1}


class TestEmotion:
    def test_emotional_parenthetical_carries_to_dialogue(self) -> None:
        graph = normalize(
            [
                slug("INT. OFFICE - DAY"),
                cue("MARA"),
                paren("(angrily)"),
                dialogue("Get out."),
            ]
        )
        paren_line, dialogue_line = graph.scenes[0].lines
        assert paren_line.kind == "parenthetical"
        assert paren_line.emotion == "angry"
        assert dialogue_line.kind == "dialogue"
        assert dialogue_line.emotion == "angry"

    def test_non_emotional_parenthetical_leaves_dialogue_neutral(self) -> None:
        graph = normalize(
            [
                slug("INT. OFFICE - DAY"),
                cue("MARA"),
                paren("(beat)"),
                dialogue("Get out."),
            ]
        )
        paren_line, dialogue_line = graph.scenes[0].lines
        assert paren_line.emotion is None
        assert dialogue_line.emotion is None

    def test_emotion_resets_on_new_cue(self) -> None:
        graph = normalize(
            [
                slug("INT. OFFICE - DAY"),
                cue("MARA"),
                paren("(angrily)"),
                dialogue("Get out."),
                cue("TOM"),
                dialogue("Calm down."),
            ]
        )
        dialogue_lines = [
            line for line in graph.scenes[0].lines if line.kind == "dialogue"
        ]
        assert dialogue_lines[0].emotion == "angry"
        assert dialogue_lines[1].emotion is None

    def test_emotion_resets_after_action_line(self) -> None:
        graph = normalize(
            [
                slug("INT. OFFICE - DAY"),
                cue("MARA"),
                paren("(angrily)"),
                dialogue("Get out."),
                action("She storms off."),
                cue("MARA", "CONT'D"),
                dialogue("Wait."),
            ]
        )
        dialogue_lines = [
            line for line in graph.scenes[0].lines if line.kind == "dialogue"
        ]
        assert dialogue_lines[0].emotion == "angry"
        assert dialogue_lines[1].emotion is None


class TestFountainIntegration:
    def test_sample_fountain_normalizes_end_to_end(
        self, sample_fountain: str
    ) -> None:
        graph = normalize(parse_fountain(sample_fountain))
        assert isinstance(graph, StoryGraph)
        assert [s.ordinal for s in graph.scenes] == [0, 1, 2, 3]

        preamble = graph.scenes[0]
        assert preamble.slugline is None
        assert [line.kind for line in preamble.lines] == ["transition"]

        keeper = graph.scenes[2]
        assert keeper.interior is True
        assert keeper.location == "LIGHTHOUSE - KEEPER'S ROOM"
        assert keeper.time_of_day == "NIGHT"

        counts = {c.canonical_name: c.line_count for c in graph.characters}
        assert counts == {"MARA": 4, "TOM": 3}

        cliff = graph.scenes[3]
        vo_line = next(line for line in cliff.lines if line.kind == "dialogue")
        assert vo_line.character_name == "MARA"
        assert vo_line.attribution_source == "cue"
        assert vo_line.attribution_confidence == 1.0
