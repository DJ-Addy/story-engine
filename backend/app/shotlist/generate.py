"""LLM shot-list generation orchestration (PRD §4.3).

Builds the prompt contract, calls the LLM, parses via the repair layer, and
retries up to ``max_retries`` times with validation errors fed back into the
user prompt. Exhausted retries surface as status='failed' for manual
authoring — generation never crashes on bad LLM output.

Coverage gaps found after a schema-valid parse do NOT fail generation; they
are returned on the result so the caller can regenerate ONLY the gap (PRD:
never regenerate the whole scene).
"""

from dataclasses import dataclass, field
from typing import Literal

from app.adapters.base import LLMProvider
from app.ingest.elements import NormalizedScene
from app.shotlist.coverage import coverage_gaps, uncovered_lines
from app.shotlist.repair import SchemaFailure, parse_llm_shotlist
from app.shotlist.schema import SceneShotList, ShotSpec

_SYSTEM_PROMPT = """\
You are a cinematography assistant that plans shot coverage for one scene of \
a screenplay. Respond with ONLY JSON — no prose, no markdown fences.

The JSON object must match this schema exactly:
{
  "scene_ordinal": <int, the scene's ordinal>,
  "action_axis": <string describing the 180-degree action axis>,
  "shots": [
    {
      "ordinal": <int>,
      "size": one of "ecu" | "cu" | "mcu" | "ms" | "mws" | "ws" | "ews" | "insert" | "pov",
      "subjects": [<character names in frame>],
      "axis_side": one of "a" | "b" | "neutral",
      "lens_mm": <int, 8 to 300>,
      "camera_height": one of "low" | "eye" | "high" | "overhead",
      "movement": one of "static" | "pan" | "tilt" | "dolly" | "handheld" | "crane",
      "eyeline": one of "left" | "right" | "to_camera" | "none",
      "covers_lines": [<line ordinals this shot covers>],
      "intent": <string, max 200 chars, why this shot exists>
    }
  ]
}

Rules:
- At most 40 shots.
- Shot ordinals must be contiguous starting from 1 (1, 2, 3, ...) with no duplicates.
- Every dialogue line ordinal in the scene must appear in some shot's covers_lines.
- Output ONLY JSON."""


@dataclass
class ShotlistGenResult:
    """Outcome of one scene's shot-list generation."""

    status: Literal["ok", "failed"]
    shot_list: SceneShotList | None
    attempts: int
    errors: list[str] = field(default_factory=list)
    uncovered: list[int] = field(default_factory=list)
    gaps: list[tuple[int, int]] = field(default_factory=list)


def _format_line(line) -> str:
    tag = f"{line.kind}/{line.character_name}" if line.character_name else line.kind
    return f"{line.ordinal}: [{tag}] {line.text}"


def build_shotlist_prompt(
    scene: NormalizedScene,
    characters: list[str],
    grammar_profile: str,
    prev_scene_last_shot: ShotSpec | None,
) -> tuple[str, str]:
    """Build the (system, user) prompt pair for shot-list generation."""
    prev_serialized = (
        prev_scene_last_shot.model_dump_json() if prev_scene_last_shot is not None else "none"
    )
    numbered_text = "\n".join(_format_line(line) for line in scene.lines)
    user = (
        f"Scene {scene.ordinal}: {scene.slugline or '(no slugline)'}\n\n"
        f"Scene text (line ordinal: [kind/character] text):\n{numbered_text}\n\n"
        f"Characters: {', '.join(characters)}\n"
        f"Grammar profile: {grammar_profile}\n"
        f"Previous scene's final shot (for cross-scene continuity): {prev_serialized}"
    )
    return _SYSTEM_PROMPT, user


async def generate_shotlist(
    scene: NormalizedScene,
    characters: list[str],
    grammar_profile: str,
    llm: LLMProvider,
    prev_scene_last_shot: ShotSpec | None = None,
    max_retries: int = 2,
) -> ShotlistGenResult:
    """Generate a shot list for one scene, retrying on schema failures."""
    system, base_user = build_shotlist_prompt(
        scene, characters, grammar_profile, prev_scene_last_shot
    )
    dialogue_ordinals = [line.ordinal for line in scene.lines if line.kind == "dialogue"]

    user = base_user
    all_errors: list[str] = []
    attempts = 0
    while attempts < max_retries + 1:
        attempts += 1
        result = await llm.complete(system, user, {})
        parsed = parse_llm_shotlist(result.text)
        if isinstance(parsed, SchemaFailure):
            all_errors.extend(parsed.errors)
            user = (
                f"{base_user}\n\n"
                f"Your previous output failed validation: {'; '.join(parsed.errors)}\n"
                f"Fix these errors and output ONLY the corrected JSON."
            )
            continue

        return ShotlistGenResult(
            status="ok",
            shot_list=parsed,
            attempts=attempts,
            errors=all_errors,
            uncovered=uncovered_lines(parsed, dialogue_ordinals),
            gaps=coverage_gaps(parsed, dialogue_ordinals),
        )

    return ShotlistGenResult(
        status="failed",
        shot_list=None,
        attempts=attempts,
        errors=all_errors,
    )
