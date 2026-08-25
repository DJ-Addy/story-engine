"""Tests for the deterministic voice-fit judge (app/judge/voices.py).

Good casting scores high; bad casting scores low with the right findings and a
better suggested voice from the pool; the optional LLM seam only enriches the
deterministic result and never breaks it.
"""

from __future__ import annotations

import json

from app.adapters.base import Voice
from app.adapters.fake import FakeLLM
from app.ingest.elements import (
    AttributedLine,
    NormalizedCharacter,
    NormalizedScene,
    StoryGraph,
)
from app.judge.voices import judge_voice_fit, voice_axes

# A soft/warm, low-energy voice and a strong/expressive, high-energy one.
SOFT = Voice(id="soft1", name="Bella", tags=["female", "conversational", "soft", "american"])
STRONG = Voice(id="strong1", name="Domi", tags=["female", "expressive", "strong", "american"])
NARR = Voice(id="narr1", name="Guy", tags=["male", "narrator", "en-US"])
POOL = [SOFT, STRONG, NARR]


def _line(ordinal: int, kind: str, text: str, name: str | None, emotion: str | None = None):
    return AttributedLine(
        ordinal=ordinal, kind=kind, text=text, character_name=name, emotion=emotion
    )


def two_character_graph() -> StoryGraph:
    """BRUTE: 5 hot lines (angry/shouting/urgent). MARA: 4 subdued lines."""
    brute_emos = ["angry", "angry", "shouting", "angry", "urgent"]
    mara_emos = ["calm", "sad", "calm", "serious"]
    lines: list[AttributedLine] = []
    ordinal = 1
    for emo in brute_emos:
        lines.append(_line(ordinal, "dialogue", "Get out of my way!", "BRUTE", emo))
        ordinal += 1
    for emo in mara_emos:
        lines.append(_line(ordinal, "dialogue", "It's all right now.", "MARA", emo))
        ordinal += 1
    scene = NormalizedScene(
        ordinal=1, slugline="INT. HALL - NIGHT", interior=True, location="HALL",
        time_of_day="NIGHT", lines=lines,
    )
    return StoryGraph(
        scenes=[scene],
        characters=[
            NormalizedCharacter(canonical_name="BRUTE", line_count=5),
            NormalizedCharacter(canonical_name="MARA", line_count=4),
        ],
    )


class TestVoiceAxes:
    def test_soft_voice_reads_low_arousal_high_warmth(self) -> None:
        arousal, warmth = voice_axes(SOFT)
        assert arousal < 0.45
        assert warmth > 0.6

    def test_strong_voice_reads_high_arousal(self) -> None:
        arousal, _ = voice_axes(STRONG)
        assert arousal > 0.7

    def test_untagged_voice_is_neutral(self) -> None:
        arousal, warmth = voice_axes(Voice(id="x", name="X", tags=["male", "en-GB"]))
        assert arousal == 0.5 and warmth == 0.5


class TestGoodVsBadCasting:
    async def test_good_casting_scores_high(self) -> None:
        graph = two_character_graph()
        casting = {"BRUTE": STRONG, "MARA": SOFT}
        result = await judge_voice_fit(graph, casting, available_voices=POOL)

        assert result.overall_score > 0.7
        by_name = {c.character: c for c in result.characters}
        assert by_name["BRUTE"].score > 0.7
        assert by_name["MARA"].score > 0.7
        # Well-cast characters have no warn-level findings.
        assert all(
            f.severity != "warn" for c in result.characters for f in c.findings
        )
        assert by_name["BRUTE"].dominant_emotions[0] == "angry"

    async def test_bad_casting_scores_low_with_findings(self) -> None:
        graph = two_character_graph()
        casting = {"BRUTE": SOFT, "MARA": STRONG}  # both backwards
        result = await judge_voice_fit(graph, casting, available_voices=POOL)

        assert result.overall_score < 0.45
        by_name = {c.character: c for c in result.characters}

        brute = by_name["BRUTE"]
        assert brute.score < 0.45
        assert "AROUSAL_TOO_SOFT" in {f.code for f in brute.findings}
        assert "60%" in brute.findings[0].message or "%" in brute.findings[0].message

        mara = by_name["MARA"]
        assert mara.score < 0.5
        assert "AROUSAL_TOO_HOT" in {f.code for f in mara.findings}

    async def test_bad_casting_suggests_a_better_voice(self) -> None:
        graph = two_character_graph()
        casting = {"BRUTE": SOFT}
        result = await judge_voice_fit(graph, casting, available_voices=POOL)
        brute = result.characters[0]
        assert brute.suggestions, "a poor fit should surface alternatives"
        # The strong/expressive voice is the best fit for an angry character.
        assert brute.suggestions[0].voice_id == "strong1"
        # Suggestions never include the already-assigned voice.
        assert all(s.voice_id != "soft1" for s in brute.suggestions)
        # ...and are ranked best-first.
        scores = [s.score for s in brute.suggestions]
        assert scores == sorted(scores, reverse=True)

    async def test_good_casting_needs_no_suggestions(self) -> None:
        graph = two_character_graph()
        result = await judge_voice_fit(
            graph, {"BRUTE": STRONG, "MARA": SOFT}, available_voices=POOL
        )
        assert all(not c.suggestions for c in result.characters)


class TestNarratorFit:
    def _narrator_graph(self) -> StoryGraph:
        lines = [
            _line(1, "narration", "The tide came in slow.", "NARRATOR"),
            _line(2, "narration", "No one saw the light.", "NARRATOR"),
            _line(3, "narration", "The keeper waited.", "NARRATOR"),
        ]
        scene = NormalizedScene(
            ordinal=1, slugline=None, interior=None, location=None,
            time_of_day=None, lines=lines,
        )
        return StoryGraph(
            scenes=[scene],
            characters=[NormalizedCharacter(canonical_name="NARRATOR", line_count=0)],
        )

    async def test_narrator_on_non_narration_voice_is_flagged(self) -> None:
        result = await judge_voice_fit(
            self._narrator_graph(), {"NARRATOR": STRONG}, available_voices=POOL
        )
        narr = result.characters[0]
        assert "NARRATOR_MISMATCH" in {f.code for f in narr.findings}
        assert narr.score < 0.6
        assert narr.suggestions[0].voice_id == "narr1"

    async def test_narrator_on_narration_voice_fits_well(self) -> None:
        result = await judge_voice_fit(
            self._narrator_graph(), {"NARRATOR": NARR}, available_voices=POOL
        )
        narr = result.characters[0]
        assert "NARRATOR_MISMATCH" not in {f.code for f in narr.findings}
        assert narr.score > 0.85


class TestUncastAndShape:
    async def test_uncast_speaking_characters_reported(self) -> None:
        graph = two_character_graph()
        result = await judge_voice_fit(graph, {"BRUTE": STRONG}, available_voices=POOL)
        assert result.uncast_characters == ["MARA"]
        assert [c.character for c in result.characters] == ["BRUTE"]

    async def test_speaks_share_reflects_line_counts(self) -> None:
        graph = two_character_graph()
        result = await judge_voice_fit(graph, {"BRUTE": STRONG, "MARA": SOFT})
        by_name = {c.character: c for c in result.characters}
        assert by_name["BRUTE"].line_count == 5
        assert by_name["BRUTE"].speaks_share > by_name["MARA"].speaks_share


class TestLLMSeam:
    async def test_llm_none_is_pure_heuristic(self) -> None:
        graph = two_character_graph()
        casting = {"BRUTE": SOFT, "MARA": STRONG}
        a = await judge_voice_fit(graph, casting, available_voices=POOL, llm=None)
        b = await judge_voice_fit(graph, casting, available_voices=POOL)
        assert a.model_dump() == b.model_dump()

    async def test_llm_note_and_score_blend_enrich_result(self) -> None:
        graph = two_character_graph()
        casting = {"BRUTE": SOFT}
        heuristic = await judge_voice_fit(graph, casting, available_voices=POOL)
        base_score = heuristic.characters[0].score

        response = json.dumps(
            {
                "overall_note": "Casting is uneven.",
                "characters": [{"character": "BRUTE", "note": "Far too soft.", "score": 0.2}],
            }
        )
        result = await judge_voice_fit(
            graph, casting, available_voices=POOL, llm=FakeLLM(response=response)
        )
        brute = result.characters[0]
        assert "LLM: Far too soft." in brute.rationale
        assert "LLM: Casting is uneven." in result.rationale
        assert brute.score == round(0.5 * base_score + 0.5 * 0.2, 3)

    async def test_garbage_llm_output_leaves_heuristic_untouched(self) -> None:
        graph = two_character_graph()
        casting = {"BRUTE": SOFT, "MARA": STRONG}
        heuristic = await judge_voice_fit(graph, casting, available_voices=POOL)
        with_llm = await judge_voice_fit(
            graph, casting, available_voices=POOL, llm=FakeLLM(response="not json at all")
        )
        assert with_llm.model_dump() == heuristic.model_dump()
