"""Tests for LLM shot-list generation orchestration (app/shotlist/generate.py)."""

import json

from app.adapters.fake import FakeLLM
from app.ingest.elements import AttributedLine, NormalizedScene
from app.shotlist.generate import build_shotlist_prompt, generate_shotlist
from app.shotlist.schema import SceneShotList, ShotSpec


class ScriptedLLM(FakeLLM):
    """FakeLLM subclass that returns a different canned response per call and
    records every (system, user) pair it receives."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__()
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    async def complete(self, system: str, user: str, params: dict):
        self.calls.append((system, user))
        index = min(len(self.calls) - 1, len(self._responses) - 1)
        self._response = self._responses[index]
        return await super().complete(system, user, params)


def make_scene() -> NormalizedScene:
    return NormalizedScene(
        ordinal=3,
        slugline="INT. LIGHTHOUSE - NIGHT",
        interior=True,
        location="LIGHTHOUSE",
        time_of_day="NIGHT",
        lines=[
            AttributedLine(ordinal=1, kind="action", text="Waves crash against the rocks."),
            AttributedLine(
                ordinal=2, kind="dialogue", text="We can't stay here.", character_name="ALICE"
            ),
            AttributedLine(
                ordinal=3, kind="dialogue", text="We have no choice.", character_name="BOB"
            ),
        ],
    )


def make_shot(ordinal: int, covers: list[int]) -> dict:
    return {
        "ordinal": ordinal,
        "size": "mcu",
        "subjects": ["ALICE"],
        "axis_side": "a",
        "lens_mm": 50,
        "camera_height": "eye",
        "movement": "static",
        "eyeline": "left",
        "covers_lines": covers,
        "intent": "coverage",
    }


def valid_shotlist_json(covers_by_shot: list[list[int]] | None = None) -> str:
    covers_by_shot = covers_by_shot if covers_by_shot is not None else [[1, 2], [3]]
    return json.dumps(
        {
            "scene_ordinal": 3,
            "action_axis": "ALICE-BOB",
            "shots": [make_shot(i + 1, covers) for i, covers in enumerate(covers_by_shot)],
        }
    )


PREV_SHOT = ShotSpec(
    ordinal=7,
    size="ws",
    subjects=["ALICE", "BOB"],
    axis_side="b",
    lens_mm=24,
    camera_height="high",
    movement="crane",
    eyeline="none",
    covers_lines=[9],
    intent="scene out",
)


class TestBuildShotlistPrompt:
    def test_returns_system_and_user(self) -> None:
        system, user = build_shotlist_prompt(make_scene(), ["ALICE", "BOB"], "classical", None)
        assert isinstance(system, str) and isinstance(user, str)
        assert system and user

    def test_system_enumerates_schema_constraints(self) -> None:
        system, _ = build_shotlist_prompt(make_scene(), ["ALICE", "BOB"], "classical", None)
        for size in ("ecu", "cu", "mcu", "ms", "mws", "ws", "ews", "insert", "pov"):
            assert size in system
        for value in ("a", "b", "neutral", "low", "eye", "high", "overhead"):
            assert value in system
        for value in ("static", "pan", "tilt", "dolly", "handheld", "crane"):
            assert value in system
        for value in ("left", "right", "to_camera", "none"):
            assert value in system
        assert "8" in system and "300" in system
        assert "40" in system
        assert "contiguous" in system.lower()
        assert "covers_lines" in system
        assert "only json" in system.lower()

    def test_user_contains_numbered_scene_lines(self) -> None:
        _, user = build_shotlist_prompt(make_scene(), ["ALICE", "BOB"], "classical", None)
        assert "1: [action] Waves crash against the rocks." in user
        assert "2: [dialogue/ALICE] We can't stay here." in user
        assert "3: [dialogue/BOB] We have no choice." in user

    def test_user_contains_scene_header_characters_and_profile(self) -> None:
        _, user = build_shotlist_prompt(make_scene(), ["ALICE", "BOB"], "handheld", None)
        assert "3" in user
        assert "INT. LIGHTHOUSE - NIGHT" in user
        assert "ALICE, BOB" in user
        assert "handheld" in user

    def test_user_serializes_prev_shot_or_none(self) -> None:
        _, user_none = build_shotlist_prompt(make_scene(), ["ALICE"], "classical", None)
        assert "none" in user_none.lower()
        _, user_prev = build_shotlist_prompt(make_scene(), ["ALICE"], "classical", PREV_SHOT)
        assert '"size":"ws"' in user_prev.replace(" ", "")
        assert '"lens_mm":24' in user_prev.replace(" ", "")


class TestGenerateShotlist:
    async def test_valid_first_try(self) -> None:
        llm = ScriptedLLM([valid_shotlist_json()])
        result = await generate_shotlist(make_scene(), ["ALICE", "BOB"], "classical", llm)
        assert result.status == "ok"
        assert result.attempts == 1
        assert isinstance(result.shot_list, SceneShotList)
        assert result.errors == []
        assert result.uncovered == []
        assert result.gaps == []

    async def test_invalid_then_valid_retries_with_error_feedback(self) -> None:
        llm = ScriptedLLM(["this is not json at all", valid_shotlist_json()])
        result = await generate_shotlist(make_scene(), ["ALICE", "BOB"], "classical", llm)
        assert result.status == "ok"
        assert result.attempts == 2
        assert isinstance(result.shot_list, SceneShotList)
        assert len(llm.calls) == 2
        second_user = llm.calls[1][1]
        assert "Your previous output failed validation" in second_user
        assert "no JSON object found in LLM output" in second_user

    async def test_always_invalid_fails_after_three_attempts(self) -> None:
        llm = ScriptedLLM(["garbage"])
        result = await generate_shotlist(make_scene(), ["ALICE", "BOB"], "classical", llm)
        assert result.status == "failed"
        assert result.attempts == 3
        assert result.shot_list is None
        assert len(llm.calls) == 3
        assert result.errors  # surfaced for manual authoring, never crash

    async def test_coverage_gap_does_not_fail_generation(self) -> None:
        # Valid shot list but only covers dialogue line 2; line 3 is a gap.
        llm = ScriptedLLM([valid_shotlist_json([[1, 2]])])
        result = await generate_shotlist(make_scene(), ["ALICE", "BOB"], "classical", llm)
        assert result.status == "ok"
        assert result.uncovered == [3]
        assert result.gaps == [(3, 3)]

    async def test_prev_shot_flows_into_prompt(self) -> None:
        llm = ScriptedLLM([valid_shotlist_json()])
        await generate_shotlist(
            make_scene(), ["ALICE", "BOB"], "classical", llm, prev_scene_last_shot=PREV_SHOT
        )
        first_user = llm.calls[0][1]
        assert '"lens_mm":24' in first_user.replace(" ", "")

    async def test_plain_fakellm_satisfies_provider_protocol(self) -> None:
        llm = FakeLLM(response=valid_shotlist_json())
        result = await generate_shotlist(make_scene(), ["ALICE", "BOB"], "classical", llm)
        assert result.status == "ok"
        assert result.attempts == 1
