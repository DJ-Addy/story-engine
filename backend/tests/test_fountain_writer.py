"""Tests for the novel -> Fountain emitter (app.ingest.fountain_writer).

Round-trip contract: emitted Fountain parses back through parse_fountain +
normalize with every quote attributed to its cue at confidence 1.0 / 'cue';
the novel conversion's own confidence lives in NovelConversionResult.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from app.adapters.base import LLMResult
from app.adapters.fake import FakeLLM
from app.ingest.elements import ElementKind
from app.ingest.fountain import parse_fountain
from app.ingest.fountain_writer import NovelScene, novel_to_fountain
from app.ingest.normalize import normalize
from app.ingest.novel import AttributedQuote, novel_to_screenplay


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


# See test_novel.py for the fixture's global quote indices.
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


def _quote(
    text: str,
    speaker: str | None = None,
    confidence: float = 0.0,
    source: str | None = None,
) -> AttributedQuote:
    return AttributedQuote(text=text, speaker=speaker, confidence=confidence, source=source)


class TestNovelToFountain:
    def test_non_slug_hints_become_forced_sluglines(self) -> None:
        scenes = [
            NovelScene(heading_hint="CHAPTER ONE", items=["The lamp burned low."]),
            NovelScene(heading_hint=None, items=["Dawn came grey."]),
        ]
        out = novel_to_fountain(scenes)
        assert ".SCENE 1" in out
        assert ".SCENE 2" in out
        assert "CHAPTER ONE" not in out

    def test_slug_like_hint_passes_through(self) -> None:
        scenes = [NovelScene(heading_hint="INT. LIGHTHOUSE - NIGHT", items=["Rain."])]
        out = novel_to_fountain(scenes)
        assert "INT. LIGHTHOUSE - NIGHT" in out
        elements = parse_fountain(out)
        assert elements[0].kind is ElementKind.SLUGLINE
        assert elements[0].text == "INT. LIGHTHOUSE - NIGHT"

    def test_quote_becomes_cue_and_dialogue(self) -> None:
        scenes = [
            NovelScene(items=[_quote("We go tonight.", "Mara", 0.8, "tag")])
        ]
        out = novel_to_fountain(scenes)
        assert "MARA\nWe go tonight." in out

    def test_unattributed_quote_uses_unknown_speaker(self) -> None:
        scenes = [NovelScene(items=[_quote("Who is there?")])]
        out = novel_to_fountain(scenes)
        assert "UNKNOWN SPEAKER\nWho is there?" in out

    def test_title_page_from_metadata(self) -> None:
        scenes = [NovelScene(items=["Rain over the harbor."])]
        out = novel_to_fountain(scenes, title="The Signal Fire", author="A. Keeper")
        assert out.startswith("Title: The Signal Fire\nAuthor: A. Keeper\n\n")
        # parse_fountain treats it as a title page, not screenplay content.
        elements = parse_fountain(out)
        assert all("Signal Fire" not in e.text for e in elements)

    def test_long_prose_wraps_into_action_lines(self) -> None:
        prose = " ".join(["the lantern turned slowly over the water"] * 8)
        out = novel_to_fountain([NovelScene(items=[prose])])
        assert all(len(line) <= 78 for line in out.splitlines())
        kinds = {e.kind for e in parse_fountain(out)}
        assert kinds == {ElementKind.SLUGLINE, ElementKind.ACTION}

    def test_round_trip_attributes_dialogue_at_full_confidence(self) -> None:
        scenes = [
            NovelScene(
                heading_hint="INT. LIGHTHOUSE - NIGHT",
                items=[
                    "Mara climbs the spiral stairs.",
                    _quote("We go tonight.", "Mara", 0.8, "tag"),
                    _quote("Who is there?"),
                ],
            )
        ]
        graph = normalize(parse_fountain(novel_to_fountain(scenes)))
        assert len(graph.scenes) == 1
        dialogue = [line for line in graph.scenes[0].lines if line.kind == "dialogue"]
        assert [
            (line.character_name, line.attribution_confidence, line.attribution_source)
            for line in dialogue
        ] == [
            ("MARA", 1.0, "cue"),
            ("UNKNOWN SPEAKER", 1.0, "cue"),
        ]


class TestFixtureRoundTrip:
    async def test_round_trip_without_llm(self, sample_novel: str) -> None:
        result = await novel_to_screenplay(sample_novel)
        graph = normalize(parse_fountain(result.fountain_text))

        assert len(graph.scenes) == 3
        assert {c.canonical_name for c in graph.characters} == {
            "MARA",
            "TOM",
            "UNKNOWN SPEAKER",
        }

        dialogue = [
            line
            for scene in graph.scenes
            for line in scene.lines
            if line.kind == "dialogue"
        ]
        assert len(dialogue) == result.quotes == 10
        assert all(
            line.attribution_confidence == 1.0 and line.attribution_source == "cue"
            for line in dialogue
        )
        counts = Counter(line.character_name for line in dialogue)
        assert counts == Counter({"MARA": 4, "TOM": 4, "UNKNOWN SPEAKER": 2})
        # needs_review also counts low-confidence *attributed* quotes, so it
        # exceeds the un-cued (UNKNOWN SPEAKER) count when no LLM repair ran.
        assert counts["UNKNOWN SPEAKER"] == 2
        assert result.needs_review == 6

    async def test_round_trip_with_llm_repair(self, sample_novel: str) -> None:
        llm = ScriptedLLM(FIXTURE_REPAIR)
        result = await novel_to_screenplay(sample_novel, llm=llm)
        graph = normalize(parse_fountain(result.fountain_text))

        dialogue = [
            line
            for scene in graph.scenes
            for line in scene.lines
            if line.kind == "dialogue"
        ]
        counts = Counter(line.character_name for line in dialogue)
        # After repair, every quote at/above threshold; the single quote the
        # scripted LLM returned null for is the only un-cued one, and
        # needs_review matches it exactly.
        assert counts["MARA"] == 5
        assert counts["TOM"] == 4
        assert counts["UNKNOWN SPEAKER"] == result.needs_review == 1
