"""Continuity rules. Each rule is a pure generator over (scene, shots).

Shots are always passed in ordinal order by the validator.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import pairwise
from typing import Any

from .model import SceneContext, ShotMeta
from .profiles import GRAMMAR_PROFILES
from .registry import rule

CLOSE_SIZES = {"ecu", "cu", "mcu"}
SIDED = {"a", "b"}
LATERAL_EYELINES = {"left", "right"}
LENS_JUMP_THRESHOLD_MM = 60


@rule("AXIS_CROSS", "warn", {"classical", "anime"})
def axis_cross(scene: SceneContext, shots: list[ShotMeta]) -> Iterator[dict[str, Any]]:
    current_side: str | None = None
    current_ordinal: int | None = None
    for s in shots:
        if s.axis_side == "crossing":
            # Declared re-establish: forget the tracked side entirely.
            current_side = None
            current_ordinal = None
            continue
        if s.axis_side not in SIDED:
            continue
        if current_side is not None and s.axis_side != current_side:
            yield {
                "message": (
                    f"Shot {s.ordinal} is on side '{s.axis_side}' but shot "
                    f"{current_ordinal} established side '{current_side}' (axis cross)."
                ),
                "shot_ordinal": s.ordinal,
            }
        current_side = s.axis_side
        current_ordinal = s.ordinal


@rule("EYELINE_MISMATCH", "warn", {"classical"})
def eyeline_mismatch(scene: SceneContext, shots: list[ShotMeta]) -> Iterator[dict[str, Any]]:
    for a, b in pairwise(shots):
        is_reverse = (
            a.size in CLOSE_SIZES
            and b.size in CLOSE_SIZES
            and len(a.subject_ids) == 1
            and len(b.subject_ids) == 1
            and a.subject_ids[0] != b.subject_ids[0]
        )
        if is_reverse and a.eyeline == b.eyeline and a.eyeline in LATERAL_EYELINES:
            yield {
                "message": (
                    f"Reverse shots {a.ordinal} and {b.ordinal} both look "
                    f"'{a.eyeline}'; eyelines in a reverse should oppose."
                ),
                "shot_ordinal": b.ordinal,
            }


@rule("NO_REVERSE", "info", {"classical"})
def no_reverse(scene: SceneContext, shots: list[ShotMeta]) -> Iterator[dict[str, Any]]:
    covered: set[str] = set()
    for s in shots:
        if s.size in CLOSE_SIZES:
            covered.update(s.subject_ids)
    for speaker in dict.fromkeys(scene.dialogue_speakers):
        if speaker not in covered:
            name = scene.character_names.get(speaker, speaker)
            yield {
                "message": f"{name} speaks but has no close coverage (ecu/cu/mcu).",
                "shot_ordinal": None,
            }


@rule("LENS_JUMP", "info", {"classical", "symmetrical"})
def lens_jump(scene: SceneContext, shots: list[ShotMeta]) -> Iterator[dict[str, Any]]:
    for a, b in pairwise(shots):
        if (
            a.lens_mm is not None
            and b.lens_mm is not None
            and a.size == b.size
            and abs(a.lens_mm - b.lens_mm) > LENS_JUMP_THRESHOLD_MM
        ):
            yield {
                "message": (
                    f"Lens jumps from {a.lens_mm}mm (shot {a.ordinal}) to "
                    f"{b.lens_mm}mm (shot {b.ordinal}) at the same size '{a.size}'."
                ),
                "shot_ordinal": b.ordinal,
            }


@rule("SCREEN_DIRECTION_FLIP", "warn", {"classical", "anime"})
def screen_direction_flip(
    scene: SceneContext, shots: list[ShotMeta]
) -> Iterator[dict[str, Any]]:
    for a, b in pairwise(shots):
        if (
            set(a.subject_ids) & set(b.subject_ids)
            and {a.eyeline, b.eyeline} == LATERAL_EYELINES
            and a.axis_side != "crossing"
            and b.axis_side != "crossing"
        ):
            yield {
                "message": (
                    f"Screen direction flips between shots {a.ordinal} and "
                    f"{b.ordinal} for a shared subject."
                ),
                "shot_ordinal": b.ordinal,
            }


@rule("TIME_OF_DAY_DRIFT", "info", GRAMMAR_PROFILES)
def time_of_day_drift(
    scene: SceneContext, shots: list[ShotMeta]
) -> Iterator[dict[str, Any]]:
    prev = scene.prev_scene_time_of_day
    curr = scene.time_of_day
    if prev is not None and curr is not None and prev != curr:
        yield {
            "message": f"Time of day drifts from '{prev}' to '{curr}' between scenes.",
            "shot_ordinal": None,
        }
