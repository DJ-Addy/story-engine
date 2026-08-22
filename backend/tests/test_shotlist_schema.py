"""Tests for the shot-list schema contract (app.shotlist.schema)."""

from typing import Any

import pytest
from pydantic import ValidationError

from app.shotlist.schema import SceneShotList, ShotSpec


def make_shot(ordinal: int = 1, **overrides: Any) -> dict[str, Any]:
    shot: dict[str, Any] = {
        "ordinal": ordinal,
        "size": "cu",
        "subjects": ["ALICE"],
        "axis_side": "a",
        "lens_mm": 50,
        "camera_height": "eye",
        "movement": "static",
        "eyeline": "left",
        "covers_lines": [ordinal],
        "intent": "Hold on Alice as she hears the news.",
    }
    shot.update(overrides)
    return shot


def make_scene(shots: list[dict[str, Any]] | None = None, **overrides: Any) -> dict[str, Any]:
    scene: dict[str, Any] = {
        "scene_ordinal": 1,
        "action_axis": "Alice faces Bob across the kitchen table.",
        "shots": shots if shots is not None else [make_shot(1), make_shot(2)],
    }
    scene.update(overrides)
    return scene


class TestShotSpec:
    def test_valid_shot_parses(self) -> None:
        shot = ShotSpec.model_validate(make_shot())
        assert shot.ordinal == 1
        assert shot.size == "cu"
        assert shot.subjects == ["ALICE"]
        assert shot.axis_side == "a"
        assert shot.lens_mm == 50
        assert shot.camera_height == "eye"
        assert shot.movement == "static"
        assert shot.eyeline == "left"
        assert shot.covers_lines == [1]

    def test_lens_mm_below_minimum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ShotSpec.model_validate(make_shot(lens_mm=7))

    def test_lens_mm_above_maximum_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ShotSpec.model_validate(make_shot(lens_mm=301))

    def test_lens_mm_boundaries_accepted(self) -> None:
        assert ShotSpec.model_validate(make_shot(lens_mm=8)).lens_mm == 8
        assert ShotSpec.model_validate(make_shot(lens_mm=300)).lens_mm == 300

    def test_ordinal_must_be_ge_one(self) -> None:
        with pytest.raises(ValidationError):
            ShotSpec.model_validate(make_shot(ordinal=0))

    def test_invalid_size_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ShotSpec.model_validate(make_shot(size="wide"))

    def test_intent_over_200_chars_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ShotSpec.model_validate(make_shot(intent="x" * 201))

    def test_intent_exactly_200_chars_accepted(self) -> None:
        shot = ShotSpec.model_validate(make_shot(intent="x" * 200))
        assert len(shot.intent) == 200


class TestSceneShotList:
    def test_valid_payload_parses(self) -> None:
        scene = SceneShotList.model_validate(make_scene())
        assert scene.scene_ordinal == 1
        assert len(scene.shots) == 2
        assert [s.ordinal for s in scene.shots] == [1, 2]

    def test_empty_shots_rejected(self) -> None:
        with pytest.raises(ValidationError):
            SceneShotList.model_validate(make_scene(shots=[]))

    def test_41_shots_rejected(self) -> None:
        shots = [make_shot(i) for i in range(1, 42)]
        with pytest.raises(ValidationError):
            SceneShotList.model_validate(make_scene(shots=shots))

    def test_40_shots_accepted(self) -> None:
        shots = [make_shot(i) for i in range(1, 41)]
        scene = SceneShotList.model_validate(make_scene(shots=shots))
        assert len(scene.shots) == 40

    def test_duplicate_ordinals_rejected(self) -> None:
        shots = [make_shot(1), make_shot(1)]
        with pytest.raises(ValidationError, match="duplicate"):
            SceneShotList.model_validate(make_scene(shots=shots))

    def test_gap_in_ordinals_rejected(self) -> None:
        shots = [make_shot(1), make_shot(3)]
        with pytest.raises(ValidationError, match="contiguous"):
            SceneShotList.model_validate(make_scene(shots=shots))

    def test_ordinals_not_starting_at_one_rejected(self) -> None:
        shots = [make_shot(2), make_shot(3)]
        with pytest.raises(ValidationError, match="contiguous"):
            SceneShotList.model_validate(make_scene(shots=shots))

    def test_out_of_order_but_contiguous_ordinals_accepted(self) -> None:
        shots = [make_shot(2), make_shot(1)]
        scene = SceneShotList.model_validate(make_scene(shots=shots))
        assert sorted(s.ordinal for s in scene.shots) == [1, 2]
