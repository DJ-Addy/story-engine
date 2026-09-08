"""Tests for the casting proposer (app/judge/casting.py).

The proposer is the step before the judge: it reads a story graph and a voice
catalogue and decides, for the narrator and every character, which voice reads
the part and in what tone. Everything asserted here is a promise the feature
makes to whoever renders off the back of it — the same inputs give the same
cast, no voice or tone is ever invented, and a part the script says nothing
about gets an honest ``None`` instead of a confident guess.
"""

from __future__ import annotations

import pytest

from app.adapters.base import Voice
from app.adapters.fake import FakeTTS
from app.ingest.elements import (
    AttributedLine,
    NormalizedCharacter,
    NormalizedScene,
    StoryGraph,
)
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize
from app.judge.casting import propose_casting_with_tone, text_tone_cue
from app.nlp.emotion import EMOTIONS

SOFT = Voice(id="soft1", name="Bella", tags=["female", "soft", "conversational"])
STRONG = Voice(id="strong1", name="Domi", tags=["female", "expressive", "strong"])
NARR = Voice(id="narr1", name="Guy", tags=["male", "narrator", "en-US"])
CALM = Voice(id="calm1", name="Ash", tags=["male", "calm", "warm"])
POOL = [SOFT, STRONG, NARR, CALM]


def _line(ordinal: int, kind: str, text: str, name: str | None, emotion: str | None = None):
    return AttributedLine(
        ordinal=ordinal, kind=kind, text=text, character_name=name, emotion=emotion
    )


def _graph(lines: list[AttributedLine], characters: list[str]) -> StoryGraph:
    scene = NormalizedScene(
        ordinal=1,
        slugline="INT. HALL - NIGHT",
        interior=True,
        location="HALL",
        time_of_day="NIGHT",
        lines=lines,
    )
    counts = {name: 0 for name in characters}
    for line in lines:
        if line.kind == "dialogue" and line.character_name in counts:
            counts[line.character_name] += 1
    return StoryGraph(
        scenes=[scene],
        characters=[
            NormalizedCharacter(canonical_name=name, line_count=counts[name])
            for name in characters
        ],
    )


def tagged_graph() -> StoryGraph:
    """The screenplay case: ingest captured a parenthetical on most lines."""
    brute = ["angry", "angry", "shouting", "angry", "urgent"]
    mara = ["calm", "sad", "calm", "serious"]
    lines = [_line(1, "action", "Rain hammers the cobblestones.", None)]
    ordinal = 2
    for emotion in brute:
        lines.append(_line(ordinal, "dialogue", "Out of my way.", "BRUTE", emotion))
        ordinal += 1
    for emotion in mara:
        lines.append(_line(ordinal, "dialogue", "It is all right now.", "MARA", emotion))
        ordinal += 1
    return _graph(lines, ["BRUTE", "MARA"])


def untagged_graph() -> StoryGraph:
    """Prose with nothing to read: no parentheticals and no tonal cue in the words."""
    lines = [
        _line(1, "action", "The door is closed.", None),
        _line(2, "dialogue", "He left the key on the table.", "MARA"),
        _line(3, "dialogue", "The tide comes in at four.", "MARA"),
        _line(4, "dialogue", "I will take the second watch.", "TOM"),
    ]
    return _graph(lines, ["MARA", "TOM"])


def shouted_graph() -> StoryGraph:
    """No tags, but the page itself shouts — the text-cue fallback tier."""
    lines = [
        _line(1, "dialogue", "GET OUT OF MY WAY!", "BRUTE"),
        _line(2, "dialogue", "I SAID MOVE ASIDE!", "BRUTE"),
        _line(3, "dialogue", "NOBODY TOUCHES THE BOAT!", "BRUTE"),
        _line(4, "dialogue", "He never listens to reason.", "BRUTE"),
    ]
    return _graph(lines, ["BRUTE"])


class TestDeterminism:
    def test_same_inputs_give_an_identical_proposal(self) -> None:
        graph = tagged_graph()
        first = propose_casting_with_tone(graph, POOL)
        second = propose_casting_with_tone(graph, POOL)
        assert first.model_dump() == second.model_dump()

    def test_catalogue_order_does_not_change_the_cast(self) -> None:
        """A provider that lists its roster in a different order casts the same show."""
        graph = tagged_graph()
        forward = propose_casting_with_tone(graph, POOL)
        backward = propose_casting_with_tone(graph, list(reversed(POOL)))
        assert forward.model_dump() == backward.model_dump()

    def test_duplicate_voices_are_collapsed(self) -> None:
        graph = tagged_graph()
        plain = propose_casting_with_tone(graph, POOL)
        doubled = propose_casting_with_tone(graph, [*POOL, SOFT, NARR])
        assert plain.model_dump() == doubled.model_dump()


class TestNothingIsInvented:
    def test_every_voice_id_exists_in_the_catalogue(self) -> None:
        catalogue = {voice.id for voice in POOL}
        for graph in (tagged_graph(), untagged_graph(), shouted_graph()):
            proposal = propose_casting_with_tone(graph, POOL)
            assert {entry.voice_id for entry in proposal.entries} <= catalogue

    def test_every_voice_name_matches_its_id(self) -> None:
        names = {voice.id: voice.name for voice in POOL}
        proposal = propose_casting_with_tone(tagged_graph(), POOL)
        assert all(entry.voice_name == names[entry.voice_id] for entry in proposal.entries)

    def test_every_tone_is_canonical_or_none(self) -> None:
        for graph in (tagged_graph(), untagged_graph(), shouted_graph()):
            proposal = propose_casting_with_tone(graph, POOL)
            for entry in proposal.entries:
                assert entry.tone is None or entry.tone in EMOTIONS

    def test_a_non_canonical_emotion_on_a_line_is_ignored(self) -> None:
        """A hand-edited or future ``lines.emotion`` can never leak into a tone."""
        graph = _graph(
            [_line(1, "dialogue", "The tide comes in at four.", "MARA", emotion="ecstatic")],
            ["MARA"],
        )
        mara = next(e for e in propose_casting_with_tone(graph, POOL).entries if e.character)
        assert mara.tone is None
        assert mara.tone_evidence == "none"


class TestNarrator:
    def test_narrator_prefers_a_narrator_tagged_voice(self) -> None:
        proposal = propose_casting_with_tone(tagged_graph(), POOL)
        narrator = proposal.entries[0]
        assert narrator.is_narrator
        assert narrator.character is None
        assert narrator.voice_id == NARR.id

    def test_exactly_one_narrator_entry(self) -> None:
        proposal = propose_casting_with_tone(tagged_graph(), POOL)
        assert sum(1 for entry in proposal.entries if entry.is_narrator) == 1

    def test_narrator_falls_back_to_the_general_pool(self) -> None:
        """No narrator-tagged voice is not an error — somebody still reads the action."""
        pool = [SOFT, STRONG]
        proposal = propose_casting_with_tone(tagged_graph(), pool)
        assert proposal.entries[0].voice_id in {voice.id for voice in pool}

    def test_narrator_is_cast_from_action_lines_no_character_speaks(self) -> None:
        proposal = propose_casting_with_tone(tagged_graph(), POOL)
        assert proposal.entries[0].line_count == 1  # the single action line

    async def test_fake_tts_catalogue_casts_its_narration_voice(self) -> None:
        """The suite's own provider: 'narration' counts as a narrator tag."""
        voices = await FakeTTS().list_voices()
        proposal = propose_casting_with_tone(tagged_graph(), voices)
        assert proposal.entries[0].voice_id == "fake-narrator"
        assert {e.voice_id for e in proposal.entries} <= {v.id for v in voices}


class TestVoiceSpread:
    def test_distinct_characters_get_distinct_voices(self) -> None:
        lines = [
            _line(1, "action", "The lamp gutters.", None),
            _line(2, "dialogue", "We should go.", "MARA"),
            _line(3, "dialogue", "Not yet.", "TOM"),
            _line(4, "dialogue", "The boat is loose.", "SAM"),
        ]
        proposal = propose_casting_with_tone(_graph(lines, ["MARA", "TOM", "SAM"]), POOL)
        assigned = [entry.voice_id for entry in proposal.entries]
        assert len(assigned) == 4
        assert len(set(assigned)) == 4

    def test_short_catalogue_deals_round_robin_without_inventing_voices(self) -> None:
        lines = [
            _line(1, "action", "The lamp gutters.", None),
            _line(2, "dialogue", "We should go.", "MARA"),
            _line(3, "dialogue", "Not yet.", "TOM"),
            _line(4, "dialogue", "The boat is loose.", "SAM"),
        ]
        pool = [NARR, SOFT]
        proposal = propose_casting_with_tone(_graph(lines, ["MARA", "TOM", "SAM"]), pool)
        assert {entry.voice_id for entry in proposal.entries} <= {v.id for v in pool}
        # The narrator keeps a voice of its own while the catalogue allows it.
        assert proposal.entries[0].voice_id != proposal.entries[1].voice_id

    def test_single_voice_catalogue_casts_everyone_on_it(self) -> None:
        proposal = propose_casting_with_tone(tagged_graph(), [SOFT])
        assert {entry.voice_id for entry in proposal.entries} == {SOFT.id}

    def test_empty_catalogue_raises(self) -> None:
        with pytest.raises(ValueError, match="empty voice catalogue"):
            propose_casting_with_tone(tagged_graph(), [])


class TestTone:
    def test_tone_comes_from_the_scripts_own_tags(self) -> None:
        proposal = propose_casting_with_tone(tagged_graph(), POOL)
        by_name = {entry.character: entry for entry in proposal.entries}
        brute = by_name["BRUTE"]
        assert brute.tone == "angry"  # 3 of 5 tagged lines, the plurality
        assert brute.tone_evidence == "emotion_tags"
        assert 0.4 < brute.confidence <= 0.9
        assert "delivery tag" in brute.rationale
        assert by_name["MARA"].tone == "calm"

    def test_tone_falls_back_to_the_words_when_nothing_is_tagged(self) -> None:
        brute = next(
            entry for entry in propose_casting_with_tone(shouted_graph(), POOL).entries
            if entry.character == "BRUTE"
        )
        assert brute.tone == "shouting"
        assert brute.tone_evidence == "text_cues"
        # An inference must never present itself as firmly as a written cue.
        assert 0.0 < brute.confidence <= 0.5
        assert "from the words themselves" in brute.rationale

    def test_no_signal_yields_no_tone_and_no_confidence(self) -> None:
        proposal = propose_casting_with_tone(untagged_graph(), POOL)
        for entry in proposal.entries:
            assert entry.tone is None
            assert entry.tone_evidence == "none"
            assert entry.confidence == 0.0
            # The voice is still a real, evidenced choice.
            assert entry.voice_fit > 0.0

    def test_a_single_tagged_line_is_believed_but_not_much(self) -> None:
        graph = _graph(
            [_line(1, "dialogue", "Stay down.", "MARA", emotion="whispering")], ["MARA"]
        )
        mara = next(e for e in propose_casting_with_tone(graph, POOL).entries if e.character)
        assert mara.tone == "whispering"
        assert mara.line_count == 1
        assert 0.0 < mara.confidence < 0.7

    def test_confidence_is_zero_exactly_when_there_is_no_tone(self) -> None:
        for graph in (tagged_graph(), untagged_graph(), shouted_graph()):
            for entry in propose_casting_with_tone(graph, POOL).entries:
                assert (entry.tone is None) == (entry.confidence == 0.0)


class TestTextToneCue:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("GET OUT OF MY WAY!", "shouting"),
            ("You did what?!", "surprised"),
            ("Run! The tide is turning!", "urgent"),
            ("I am scared of the water.", "afraid"),
            ("He left the key on the table.", None),
            ("", None),
            ("Quiet, the both of you.", None),
        ],
    )
    def test_cues(self, text: str, expected: str | None) -> None:
        assert text_tone_cue(text) == expected

    def test_every_cue_is_canonical(self) -> None:
        cue = text_tone_cue("STAND BACK, ALL OF YOU!")
        assert cue in EMOTIONS


class TestDegenerateGraphs:
    def test_graph_with_no_dialogue_still_casts_the_narrator(self) -> None:
        graph = _graph(
            [
                _line(1, "action", "Rain hammers the cobblestones.", None),
                _line(2, "action", "A lantern swings on the cliff path.", None),
            ],
            [],
        )
        proposal = propose_casting_with_tone(graph, POOL)
        assert len(proposal.entries) == 1
        assert proposal.entries[0].is_narrator
        assert proposal.entries[0].line_count == 2

    def test_empty_graph_still_reserves_a_narrator_voice(self) -> None:
        proposal = propose_casting_with_tone(StoryGraph(), POOL)
        assert len(proposal.entries) == 1
        assert proposal.entries[0].line_count == 0
        assert proposal.entries[0].tone is None
        assert "no spoken lines" in proposal.entries[0].rationale

    def test_declared_but_silent_character_is_still_cast(self) -> None:
        graph = StoryGraph(
            scenes=[],
            characters=[NormalizedCharacter(canonical_name="GHOST", line_count=0)],
        )
        entries = propose_casting_with_tone(graph, POOL).entries
        ghost = next(entry for entry in entries if entry.character == "GHOST")
        assert ghost.tone is None
        assert ghost.confidence == 0.0


class TestSampleScreenplay:
    def test_proposes_a_cast_for_the_sample_fountain(self, sample_fountain: str) -> None:
        graph = normalize(parse_fountain(sample_fountain))
        proposal = propose_casting_with_tone(graph, POOL)

        assert [entry.character for entry in proposal.entries] == [None, "MARA", "TOM"]
        assert proposal.entries[0].voice_id == NARR.id
        # MARA's only parenthetical is "(under her breath)", which ingest reads
        # as `whispering` — the proposer must carry that through untouched.
        mara = next(entry for entry in proposal.entries if entry.character == "MARA")
        assert mara.tone == "whispering"
        assert mara.tone_evidence == "emotion_tags"
        assert len({entry.voice_id for entry in proposal.entries}) == 3
        assert proposal.rationale

    def test_as_casting_drops_the_narrator(self, sample_fountain: str) -> None:
        graph = normalize(parse_fountain(sample_fountain))
        proposal = propose_casting_with_tone(graph, POOL)
        casting = proposal.as_casting()
        assert set(casting) == {"MARA", "TOM"}
        assert set(casting.values()) <= {voice.id for voice in POOL}

    def test_every_rationale_is_one_sentence_that_names_the_voice(
        self, sample_fountain: str
    ) -> None:
        graph = normalize(parse_fountain(sample_fountain))
        for entry in propose_casting_with_tone(graph, POOL).entries:
            assert entry.rationale.endswith(".")
            assert entry.voice_name in entry.rationale
