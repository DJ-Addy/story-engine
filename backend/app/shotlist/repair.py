"""Tolerant parsing of raw LLM output into a validated SceneShotList.

This function is pure parse + validate; the max-2-retry orchestration lives
elsewhere. It never raises — failures come back as ``SchemaFailure`` so the
caller can feed the errors into a repair prompt.
"""

import json
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app.shotlist.schema import SceneShotList

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?|\n?```\s*$", re.MULTILINE)


@dataclass
class SchemaFailure:
    """Validation or parse failure, with human-readable error strings."""

    errors: list[str] = field(default_factory=list)


def _extract_json_candidate(raw: str) -> str | None:
    """Strip markdown fences, then slice from the first '{' to the last '}'."""
    text = _FENCE_RE.sub("", raw).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]


def parse_llm_shotlist(raw: str) -> SceneShotList | SchemaFailure:
    """Parse raw LLM output into a SceneShotList, or report why it failed."""
    candidate = _extract_json_candidate(raw)
    if candidate is None:
        return SchemaFailure(errors=["no JSON object found in LLM output"])

    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return SchemaFailure(errors=[f"invalid JSON: {exc}"])

    try:
        return SceneShotList.model_validate(payload)
    except ValidationError as exc:
        return SchemaFailure(
            errors=[
                f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}"
                for err in exc.errors()
            ]
        )
