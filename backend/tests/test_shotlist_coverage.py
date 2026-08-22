"""Tests for dialogue-coverage checking (app.shotlist.coverage)."""

from typing import Any

from app.shotlist.coverage import coverage_gaps, uncovered_lines
from app.shotlist.schema import SceneShotList


def make_scene_covering(covers_per_shot: list[list[int]]) -> SceneShotList:
    shots: list[dict[str, Any]] = [
        {
            "ordinal": i,
            "size": "ms",
            "subjects": ["ALICE", "BOB"],
            "axis_side": "neutral",
            "lens_mm": 35,
            "camera_height": "eye",
            "movement": "static",
            "eyeline": "none",
            "covers_lines": covers,
            "intent": "Two-shot holding both characters.",
        }
        for i, covers in enumerate(covers_per_shot, start=1)
    ]
    return SceneShotList.model_validate(
        {
            "scene_ordinal": 1,
            "action_axis": "Alice faces Bob.",
            "shots": shots,
        }
    )


class TestUncoveredLines:
    def test_full_coverage_returns_empty(self) -> None:
        scene = make_scene_covering([[1, 2], [3, 4, 5]])
        assert uncovered_lines(scene, [1, 2, 3, 4, 5]) == []

    def test_partial_coverage_returns_exact_missing_ordinals(self) -> None:
        scene = make_scene_covering([[1, 2], [6]])
        assert uncovered_lines(scene, [1, 2, 3, 4, 5, 6]) == [3, 4, 5]

    def test_no_dialogue_lines_returns_empty(self) -> None:
        scene = make_scene_covering([[1]])
        assert uncovered_lines(scene, []) == []

    def test_overlapping_shot_coverage_counts_once(self) -> None:
        scene = make_scene_covering([[1, 2, 3], [2, 3, 4]])
        assert uncovered_lines(scene, [1, 2, 3, 4, 5]) == [5]

    def test_result_sorted_even_if_input_unsorted(self) -> None:
        scene = make_scene_covering([[2]])
        assert uncovered_lines(scene, [3, 1, 2]) == [1, 3]


class TestCoverageGaps:
    def test_full_coverage_returns_no_gaps(self) -> None:
        scene = make_scene_covering([[1, 2, 3]])
        assert coverage_gaps(scene, [1, 2, 3]) == []

    def test_consecutive_uncovered_grouped_into_ranges(self) -> None:
        # Uncovered: [3, 4, 5, 9] -> [(3, 5), (9, 9)]
        scene = make_scene_covering([[1, 2], [6, 7, 8]])
        assert coverage_gaps(scene, [1, 2, 3, 4, 5, 6, 7, 8, 9]) == [(3, 5), (9, 9)]

    def test_single_uncovered_line_is_singleton_range(self) -> None:
        scene = make_scene_covering([[1, 3]])
        assert coverage_gaps(scene, [1, 2, 3]) == [(2, 2)]

    def test_all_uncovered_is_one_range(self) -> None:
        scene = make_scene_covering([[99]])
        assert coverage_gaps(scene, [1, 2, 3]) == [(1, 3)]
