"""Tests for rule-based novel ingest (app.ingest.novel).

Attribution tiers under test (confidence / source):
- adjacent name tag ('"...," said Mara')          0.9 / tag
- action beat ('Mara set down the lamp. "..."')   0.8 / tag
- resolved pronoun tag ('"...," he said')         0.7 / tag
- untagged two-speaker alternation                0.6 / tag
- LLM repair                                      0.75 / llm
- unattributed                                    0.0 / None
"""

import json
from pathlib import Path

import pytest

from app.adapters.base import LLMResult
from app.adapters.fake import FakeLLM
from app.ingest.novel import (
    AttributedQuote,
    attribute_quotes,
    extract_segments,
    is_known_character,
    novel_to_screenplay,
    repair_attributions,
    split_into_scenes,
)


@pytest.fixture
def sample_novel(fixtures_dir: Path) -> str:
    return (fixtures_dir / "sample_novel.txt").read_text(encoding="utf-8")


class ScriptedLLM(FakeLLM):
    """FakeLLM that records every prompt it receives."""

    def __init__(self, response: str) -> None:
        super().__init__(response=response)
        self.calls: list[dict[str, str]] = []

    async def complete(self, system: str, user: str, params: dict) -> LLMResult:
        self.calls.append({"system": system, "user": user})
        return await super().complete(system, user, params)


def _quote(
    text: str,
    speaker: str | None = None,
    confidence: float = 0.0,
    source: str | None = None,
    paragraph_index: int = 0,
) -> AttributedQuote:
    return AttributedQuote(
        text=text,
        speaker=speaker,
        confidence=confidence,
        source=source,
        paragraph_index=paragraph_index,
    )


# Scripted repair for the sample_novel.txt fixture. Global quote indices:
# 0 wick/Mara .9, 1 trim/Tom .9, 2 tower/Mara .8, 3 light/Tom .7,
# 4 boat/Mara .9, 5 fishermen/Tom .6, 6 weather/Mara .6, 7 signal/Tom .6,
# 8 doors/None, 9 name/None.
FIXTURE_REPAIR = json.dumps(
    {
        "attributions": [
            {"index": 3, "speaker": "Tom"},
            {"index": 5, "speaker": "Tom"},
            {"index": 6, "speaker": "Mara"},
            {"index": 7, "speaker": "Tom"},
            {"index": 8, "speaker": "Mara"},
            {"index": 9, "speaker": None},
        ]
    }
)


class TestSplitIntoScenes:
    def test_chapter_headings_split(self) -> None:
        text = "CHAPTER ONE\n\nFirst scene text.\n\nChapter 12\n\nSecond scene text.\n"
        assert split_into_scenes(text) == ["First scene text.", "Second scene text."]

    def test_roman_numeral_headings_split(self) -> None:
        text = "I\n\nOpening text.\n\nII\n\nClosing text.\n"
        assert split_into_scenes(text) == ["Opening text.", "Closing text."]

    @pytest.mark.parametrize("marker", ["***", "* * *", "---"])
    def test_scene_break_markers_subdivide_chapters(self, marker: str) -> None:
        text = f"CHAPTER ONE\n\nBefore the break.\n\n{marker}\n\nAfter the break.\n"
        assert split_into_scenes(text) == ["Before the break.", "After the break."]

    def test_run_of_three_blank_lines_splits(self) -> None:
        text = "First block.\n\n\n\nSecond block."
        assert split_into_scenes(text) == ["First block.", "Second block."]

    def test_shorter_blank_runs_do_not_split(self) -> None:
        text = "First block.\n\n\nSecond block."
        assert len(split_into_scenes(text)) == 1

    def test_text_before_first_chapter_is_own_scene(self) -> None:
        text = "A gull cried over the harbor.\n\nCHAPTER ONE\n\nThe keeper woke."
        assert split_into_scenes(text) == [
            "A gull cried over the harbor.",
            "The keeper woke.",
        ]

    def test_fixture_yields_three_scenes(self, sample_novel: str) -> None:
        scenes = split_into_scenes(sample_novel)
        assert len(scenes) == 3
        assert "Fishermen" in scenes[1]
        assert "Nothing with a name" in scenes[2]


class TestExtractSegments:
    def test_prose_quote_prose(self) -> None:
        paragraph = 'Mara smiled. "Hello there." She waited.'
        assert extract_segments(paragraph) == [
            ("prose", "Mara smiled."),
            ("quote", "Hello there."),
            ("prose", "She waited."),
        ]

    def test_curly_quotes(self) -> None:
        paragraph = "\u201cWe go tonight,\u201d she said."
        assert extract_segments(paragraph) == [
            ("quote", "We go tonight,"),
            ("prose", "she said."),
        ]

    def test_multi_sentence_quote_stays_one_segment(self) -> None:
        paragraph = '"We go tonight. Bring the oil."'
        assert extract_segments(paragraph) == [
            ("quote", "We go tonight. Bring the oil.")
        ]

    def test_interrupted_quote_merges_into_one_logical_quote(self) -> None:
        paragraph = '"Fine," she said, "but hurry."'
        assert extract_segments(paragraph) == [
            ("quote", "Fine, but hurry."),
            ("prose", "she said,"),
        ]

    def test_no_quotes_is_single_prose_segment(self) -> None:
        paragraph = "The lamp guttered in the draught."
        assert extract_segments(paragraph) == [(
            "prose",
            "The lamp guttered in the draught.",
        )]


class TestIsKnownCharacter:
    def test_plain_name(self) -> None:
        assert is_known_character("Mara", ["Mara", "Tom"])

    def test_title_prefix(self) -> None:
        assert is_known_character("Dr. Mara", ["Mara"])

    def test_possessive(self) -> None:
        assert is_known_character("Mara's", ["Mara"])

    def test_unknown_word(self) -> None:
        assert not is_known_character("Harbor", ["Mara"])


class TestAttributeQuotes:
    def test_post_positioned_tag(self) -> None:
        quotes = attribute_quotes(['"The wick wants trimming," said Mara.'])
        assert [(q.speaker, q.confidence, q.source) for q in quotes] == [
            ("Mara", 0.9, "tag")
        ]

    def test_pre_positioned_tag(self) -> None:
        quotes = attribute_quotes(['Tom said, "Trim it yourself."'])
        assert [(q.speaker, q.confidence, q.source) for q in quotes] == [
            ("Tom", 0.9, "tag")
        ]

    def test_titled_name_in_tag_is_canonicalized(self) -> None:
        quotes = attribute_quotes(['"Hold the light steady," said Dr. Mara.'])
        assert quotes[0].speaker == "Mara"
        assert quotes[0].confidence == 0.9

    def test_interrupted_quote_is_one_quote_with_tag_attribution(self) -> None:
        quotes = attribute_quotes(['"Fine," said Mara, "but hurry."'])
        assert len(quotes) == 1
        assert quotes[0].text == "Fine, but hurry."
        assert (quotes[0].speaker, quotes[0].confidence) == ("Mara", 0.9)

    def test_pronoun_tag_resolves_to_unambiguous_paragraph_name(self) -> None:
        quotes = attribute_quotes(
            [
                '"Morning," said Tom.',
                'Beside Tom, the kettle sang. "Watch the glass," he said.',
            ]
        )
        assert (quotes[1].speaker, quotes[1].confidence, quotes[1].source) == (
            "Tom",
            0.7,
            "tag",
        )

    def test_pronoun_tag_with_ambiguous_names_stays_unattributed(self) -> None:
        quotes = attribute_quotes(
            [
                '"One," said Tom.',
                '"Two," said Mara.',
                'Tom passed Mara the lamp oil without a word. "Steady on," he said.',
            ]
        )
        assert quotes[2].speaker is None
        assert quotes[2].confidence == 0.0

    def test_pronoun_tag_with_no_paragraph_name_stays_unattributed(self) -> None:
        quotes = attribute_quotes(['"Go," he said.'])
        assert quotes[0].speaker is None
        assert quotes[0].confidence == 0.0
        assert quotes[0].source is None

    def test_action_beat_attribution(self) -> None:
        quotes = attribute_quotes(
            [
                '"Ready the boat," said Mara.',
                'Mara set down the lamp. "We go tonight."',
            ]
        )
        assert (quotes[1].speaker, quotes[1].confidence, quotes[1].source) == (
            "Mara",
            0.8,
            "tag",
        )

    def test_action_beat_requires_known_character(self) -> None:
        quotes = attribute_quotes(['Rufus set down the lamp. "We go tonight."'])
        assert quotes[0].speaker is None
        assert quotes[0].confidence == 0.0

    def test_untagged_alternation_in_two_speaker_exchange(self) -> None:
        quotes = attribute_quotes(
            [
                '"One," said Tom.',
                '"Two," said Mara.',
                '"Three."',
                '"Four."',
            ]
        )
        assert (quotes[2].speaker, quotes[2].confidence, quotes[2].source) == (
            "Tom",
            0.6,
            "tag",
        )
        assert (quotes[3].speaker, quotes[3].confidence) == ("Mara", 0.6)

    def test_alternation_broken_by_intervening_prose_paragraph(self) -> None:
        quotes = attribute_quotes(
            [
                '"One," said Tom.',
                '"Two," said Mara.',
                "The wind rattled the panes.",
                '"Three."',
            ]
        )
        assert quotes[2].speaker is None
        assert quotes[2].confidence == 0.0

    def test_alternation_requires_two_distinct_speakers(self) -> None:
        quotes = attribute_quotes(['"One," said Tom.', '"Two."'])
        assert quotes[1].speaker is None
        assert quotes[1].confidence == 0.0

    def test_fixture_attribution_tiers(self, sample_novel: str) -> None:
        paragraphs = [
            " ".join(chunk.split())
            for scene in split_into_scenes(sample_novel)
            for chunk in scene.split("\n\n")
            if chunk.strip()
        ]
        quotes = attribute_quotes(paragraphs)
        assert [(q.speaker, q.confidence) for q in quotes] == [
            ("Mara", 0.9),
            ("Tom", 0.9),
            ("Mara", 0.8),
            ("Tom", 0.7),
            ("Mara", 0.9),
            ("Tom", 0.6),
            ("Mara", 0.6),
            ("Tom", 0.6),
            (None, 0.0),
            (None, 0.0),
        ]
        assert all(q.source == "tag" for q in quotes if q.speaker is not None)
        assert all(q.source is None for q in quotes if q.speaker is None)


class TestRepairAttributions:
    async def test_batches_only_low_confidence_quotes(self) -> None:
        quotes = [
            _quote("We sail at dawn.", speaker="Mara", confidence=0.9, source="tag"),
            _quote("Who goes there?", paragraph_index=1),
        ]
        paragraphs = [
            '"We sail at dawn," said Mara.',
            'A shadow crossed the lantern room. "Who goes there?"',
        ]
        llm = ScriptedLLM(json.dumps({"attributions": [{"index": 1, "speaker": "Tom"}]}))
        await repair_attributions(quotes, paragraphs, ["Mara", "Tom"], llm)

        assert len(llm.calls) == 1
        user = llm.calls[0]["user"]
        assert "Who goes there?" in user
        assert "We sail at dawn." not in user
        # Repaired quote gets speaker at the threshold confidence, source 'llm'.
        assert (quotes[1].speaker, quotes[1].confidence, quotes[1].source) == (
            "Tom",
            0.75,
            "llm",
        )
        # High-confidence quote untouched.
        assert (quotes[0].speaker, quotes[0].confidence, quotes[0].source) == (
            "Mara",
            0.9,
            "tag",
        )

    async def test_prompt_includes_character_list(self) -> None:
        quotes = [_quote("Who goes there?")]
        llm = ScriptedLLM(json.dumps({"attributions": []}))
        await repair_attributions(quotes, ["some context"], ["Mara", "Tom"], llm)
        user = llm.calls[0]["user"]
        assert "Mara" in user
        assert "Tom" in user

    async def test_fenced_json_response_is_parsed(self) -> None:
        quotes = [_quote("Who goes there?")]
        response = (
            "```json\n"
            + json.dumps({"attributions": [{"index": 0, "speaker": "Tom"}]})
            + "\n```"
        )
        await repair_attributions(quotes, ["context"], ["Tom"], ScriptedLLM(response))
        assert quotes[0].speaker == "Tom"
        assert quotes[0].source == "llm"

    async def test_parse_failure_leaves_quotes_unchanged(self) -> None:
        quotes = [_quote("Who goes there?")]
        await repair_attributions(
            quotes, ["context"], ["Tom"], ScriptedLLM("I could not work that out.")
        )
        assert quotes[0].speaker is None
        assert quotes[0].confidence == 0.0
        assert quotes[0].source is None

    async def test_null_speaker_leaves_quote_unchanged(self) -> None:
        quotes = [_quote("Who goes there?")]
        response = json.dumps({"attributions": [{"index": 0, "speaker": None}]})
        await repair_attributions(quotes, ["context"], ["Tom"], ScriptedLLM(response))
        assert quotes[0].speaker is None
        assert quotes[0].confidence == 0.0

    async def test_no_low_confidence_quotes_skips_llm(self) -> None:
        quotes = [_quote("We sail at dawn.", speaker="Mara", confidence=0.9, source="tag")]
        llm = ScriptedLLM(json.dumps({"attributions": []}))
        await repair_attributions(quotes, ["context"], ["Mara"], llm)
        assert llm.calls == []

    async def test_confidence_at_threshold_is_not_batched(self) -> None:
        quotes = [_quote("We wait.", speaker="Tom", confidence=0.75, source="llm")]
        llm = ScriptedLLM(json.dumps({"attributions": []}))
        await repair_attributions(quotes, ["context"], ["Tom"], llm)
        assert llm.calls == []

    async def test_out_of_range_index_is_ignored(self) -> None:
        quotes = [_quote("Who goes there?")]
        response = json.dumps({"attributions": [{"index": 99, "speaker": "Tom"}]})
        await repair_attributions(quotes, ["context"], ["Tom"], ScriptedLLM(response))
        assert quotes[0].speaker is None

    async def test_provider_error_leaves_quotes_unchanged(self) -> None:
        quotes = [_quote("Who goes there?")]
        llm = FakeLLM(terminal_fail=True)
        await repair_attributions(quotes, ["context"], ["Tom"], llm)
        assert quotes[0].speaker is None
        assert quotes[0].confidence == 0.0


class TestNovelToScreenplay:
    async def test_conversion_counts_without_llm(self, sample_novel: str) -> None:
        result = await novel_to_screenplay(sample_novel)
        assert result.scenes == 3
        assert result.quotes == 10
        assert result.attributed == 8
        # 1 pronoun (0.7) + 3 alternation (0.6) + 2 unattributed.
        assert result.needs_review == 6
        assert result.characters == ["Mara", "Tom"]

    async def test_conversion_with_llm_repair(self, sample_novel: str) -> None:
        llm = ScriptedLLM(FIXTURE_REPAIR)
        result = await novel_to_screenplay(sample_novel, llm=llm)
        assert len(llm.calls) == 1
        assert result.attributed == 9
        assert result.needs_review == 1
        assert result.characters == ["Mara", "Tom"]

    async def test_repair_batch_contains_only_low_confidence_quotes(
        self, sample_novel: str
    ) -> None:
        llm = ScriptedLLM(FIXTURE_REPAIR)
        await novel_to_screenplay(sample_novel, llm=llm)
        user = llm.calls[0]["user"]
        # Low-confidence quotes are in the batch.
        assert "Fishermen, maybe." in user
        assert "Whoever built this place" in user
        # High-confidence (0.9) quotes never leave the process.
        assert "The wick wants trimming again" not in user
        assert "There's a boat out past the shoals" not in user



class TestSungDialogue:
    """A sung line is dialogue, and attributes like any other."""

    def test_sang_is_a_dialogue_verb_post_tag(self) -> None:
        quotes = attribute_quotes(['"Come here," sang Mara.'])
        assert [(q.speaker, q.confidence, q.source) for q in quotes] == [
            ("Mara", 0.9, "tag")
        ]

    def test_sang_is_a_dialogue_verb_pre_tag(self) -> None:
        """The Sirens calling to Ulysses is the case this exists for."""
        quotes = attribute_quotes(['The Sirens sang, "Come here, renowned Ulysses."'])
        assert quotes[0].speaker == "The Sirens"
        assert quotes[0].confidence == 0.9
        assert quotes[0].source == "tag"
