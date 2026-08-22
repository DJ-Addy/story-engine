"""SSE event contract for job progress streaming (PRD §5.5)."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel


class ProgressEvent(BaseModel):
    job_id: str
    progress: float
    stage: str
    completed: int
    total: int


class ChildFailedEvent(BaseModel):
    job_id: str
    child_id: str
    line_ordinal: int | None
    error: str


class DoneEvent(BaseModel):
    job_id: str
    state: Literal["succeeded", "partial", "failed"]
    cost_cents: int
    artifacts: list[str]


_EVENT_NAMES: dict[type, str] = {
    ProgressEvent: "progress",
    ChildFailedEvent: "child_failed",
    DoneEvent: "done",
}


def to_sse(event: ProgressEvent | ChildFailedEvent | DoneEvent) -> str:
    """Serialize to a wire-ready SSE frame: named event, compact JSON data,
    blank-line terminator."""
    name = _EVENT_NAMES[type(event)]
    data = json.dumps(event.model_dump(), separators=(",", ":"))
    return f"event: {name}\ndata: {data}\n\n"
