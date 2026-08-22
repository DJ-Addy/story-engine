"""Pure fan-out/fan-in job planning for scene audio renders (PRD §5.2).

No arq, no I/O: everything here is deterministic data assembly so tests never
need Redis and idempotency keys are reproducible for cache hits (PRD §4.5).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.adapters.base import TTSProvider
from app.costs.retry import idempotency_key

ChildState = Literal["succeeded", "failed"]
ParentState = Literal["succeeded", "partial", "failed"]

# JSON canonicalization in idempotency_key sorts dict keys, which breaks on a
# None (narrator) key mixed with str keys — so payloads store this sentinel.
NARRATOR_KEY = ""


class JobSpec(BaseModel):
    kind: str
    idempotency_key: str
    payload: dict
    children: list["JobSpec"] = Field(default_factory=list)
    barrier: bool = False


def plan_scene_audio_render(
    scene_ordinal: int,
    lines: list[dict],
    voice_map: dict[str | None, str],
    ambience_tags: list[str],
) -> JobSpec:
    """Fan a scene render out into per-line TTS + ambience, fanned in by a
    barrier mix job that runs only after every sibling settles."""
    children: list[JobSpec] = []
    for line in lines:
        payload = {
            "scene_ordinal": scene_ordinal,
            "line_ordinal": line["ordinal"],
            "text": line["text"],
            "voice_id": voice_map[line["character_name"]],
            "emotion": line["emotion"],
        }
        children.append(
            JobSpec(
                kind="tts_line",
                idempotency_key=idempotency_key("tts_line", payload),
                payload=payload,
            )
        )

    ambience_payload = {"scene_ordinal": scene_ordinal, "tags": list(ambience_tags)}
    children.append(
        JobSpec(
            kind="generate_ambience",
            idempotency_key=idempotency_key("generate_ambience", ambience_payload),
            payload=ambience_payload,
        )
    )

    mix_payload = {
        "scene_ordinal": scene_ordinal,
        "child_keys": [c.idempotency_key for c in children],
    }
    children.append(
        JobSpec(
            kind="mix_scene",
            idempotency_key=idempotency_key("mix_scene", mix_payload),
            payload=mix_payload,
            barrier=True,
        )
    )

    parent_payload = {
        "scene_ordinal": scene_ordinal,
        "lines": [dict(line) for line in lines],
        "voice_map": {
            (NARRATOR_KEY if name is None else name): voice
            for name, voice in voice_map.items()
        },
        "ambience_tags": list(ambience_tags),
    }
    return JobSpec(
        kind="render_scene_audio",
        idempotency_key=idempotency_key("render_scene_audio", parent_payload),
        payload=parent_payload,
        children=children,
    )


def estimate_render_cost_cents(
    lines: list[dict],
    tts_provider: TTSProvider,
    ambience_flat_cents: int = 10,
) -> int:
    """Pre-flight estimate for the governor: per-line TTS plus flat ambience."""
    return sum(tts_provider.estimate_cost_cents(line["text"]) for line in lines) + (
        ambience_flat_cents
    )


def reconcile_children(results: list[tuple[str, ChildState]]) -> ParentState:
    """PRD rule: a failed child never fails the parent outright — the UI
    re-renders only the failed children of a 'partial' parent."""
    if all(state == "succeeded" for _, state in results):
        return "succeeded"
    if any(state == "succeeded" for _, state in results):
        return "partial"
    return "failed"


def failed_children(results: list[tuple[str, ChildState]]) -> list[str]:
    """Idempotency keys of failed children, in order, for re-enqueueing."""
    return [key for key, state in results if state == "failed"]
