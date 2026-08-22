"""Domain model for the continuity validator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ShotSize = Literal["ecu", "cu", "mcu", "ms", "mws", "ws", "ews", "insert", "pov"]
AxisSide = Literal["a", "b", "neutral", "crossing"]
CameraHeight = Literal["low", "eye", "high", "overhead"]
Movement = Literal["static", "pan", "tilt", "dolly", "handheld", "crane"]
Eyeline = Literal["left", "right", "to_camera", "none"]
Severity = Literal["info", "warn", "error"]


class ShotMeta(BaseModel):
    ordinal: int
    size: ShotSize
    subject_ids: list[str] = Field(default_factory=list)
    axis_side: AxisSide = "neutral"
    lens_mm: int | None = None
    camera_height: CameraHeight | None = None
    movement: Movement | None = None
    eyeline: Eyeline | None = None


class SceneContext(BaseModel):
    ordinal: int
    dialogue_speakers: list[str] = Field(default_factory=list)
    character_names: dict[str, str] = Field(default_factory=dict)
    time_of_day: str | None = None
    prev_scene_time_of_day: str | None = None


class Finding(BaseModel):
    # rule_code and severity are stamped by the registry decorator, so rule
    # bodies may yield findings without them.
    rule_code: str = ""
    severity: Severity = "info"
    message: str
    shot_ordinal: int | None = None
