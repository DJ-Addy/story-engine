"""Pydantic request/response models for the API layer.

Story-graph payloads reuse the ingest models directly (``StoryGraphOut``),
and shot payloads reuse ``app.shotlist.schema.ShotSpec`` so the API contract
stays in lockstep with the domain modules.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.ingest.elements import StoryGraph
from app.shotlist.schema import ShotSpec

GrammarProfile = Literal["classical", "handheld", "symmetrical", "anime"]
ValidatorMode = Literal["strict", "lenient", "off"]


class UserCreate(BaseModel):
    email: str = Field(min_length=3, pattern=r".+@.+")
    password: str = Field(min_length=8)


class UserOut(BaseModel):
    id: str
    email: str


class TokenPair(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1)
    grammar_profile: GrammarProfile = "classical"
    validator_mode: ValidatorMode = "strict"
    rights_attested: bool


class ProjectOut(BaseModel):
    id: str
    owner_id: str
    title: str
    grammar_profile: str
    validator_mode: str
    rights_attested: bool
    cost_cap_cents: int = 15000
    cost_spent_cents: int = 0


class ScriptUploadOut(BaseModel):
    script_id: str
    format: str
    scene_count: int
    character_count: int


class StoryGraphOut(StoryGraph):
    """Response model for GET .../graph; identical shape to the ingest model."""


class LinePatch(BaseModel):
    """Manual attribution correction. Applying any patch stamps the line with
    attribution_source='manual' and confidence 1.0 (human edits are ground truth)."""

    character_name: str | None = None
    text: str | None = None

    @model_validator(mode="after")
    def _require_some_change(self) -> "LinePatch":
        if self.character_name is None and self.text is None:
            raise ValueError("patch must set character_name and/or text")
        return self


class ShotListOut(BaseModel):
    scene_ordinal: int
    action_axis: str
    shots: list[ShotSpec]


class FindingOut(BaseModel):
    id: str
    rule_code: str
    severity: str
    message: str
    shot_ordinal: int | None = None
    deliberate: bool = False
    deliberate_note: str | None = None


class FindingPatch(BaseModel):
    deliberate: bool
    deliberate_note: str | None = None
