"""Pydantic schema contract for LLM-generated shot lists.

The LLM must return JSON that validates against ``SceneShotList``. Anything
that fails validation is handled by the repair/retry layer, not here.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ShotSize = Literal["ecu", "cu", "mcu", "ms", "mws", "ws", "ews", "insert", "pov"]
AxisSide = Literal["a", "b", "neutral"]
CameraHeight = Literal["low", "eye", "high", "overhead"]
Movement = Literal["static", "pan", "tilt", "dolly", "handheld", "crane"]
Eyeline = Literal["left", "right", "to_camera", "none"]


class ShotSpec(BaseModel):
    """A single shot in a scene's coverage plan."""

    ordinal: int = Field(ge=1)
    size: ShotSize
    subjects: list[str]
    axis_side: AxisSide
    lens_mm: int = Field(ge=8, le=300)
    camera_height: CameraHeight
    movement: Movement
    eyeline: Eyeline
    covers_lines: list[int]
    intent: str = Field(max_length=200)


class SceneShotList(BaseModel):
    """The full shot list for one scene."""

    scene_ordinal: int
    action_axis: str
    shots: list[ShotSpec] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def _check_shot_ordinals(self) -> "SceneShotList":
        ordinals = [shot.ordinal for shot in self.shots]
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("shots contain duplicate ordinals")
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            raise ValueError(
                f"shot ordinals must be contiguous 1..{len(ordinals)}, got {sorted(ordinals)}"
            )
        return self
