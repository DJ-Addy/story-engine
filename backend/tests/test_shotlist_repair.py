"""Tests for tolerant LLM output parsing (app.shotlist.repair)."""

import json
from typing import Any

from app.shotlist.repair import SchemaFailure, parse_llm_shotlist
from app.shotlist.schema import SceneShotList


def valid_payload() -> dict[str, Any]:
    return {
        "scene_ordinal": 1,
        "action_axis": "Alice faces Bob across the kitchen table.",
        "shots": [
            {
                "ordinal": 1,
                "size": "cu",
                "subjects": ["ALICE"],
                "axis_side": "a",
                "lens_mm": 50,
                "camera_height": "eye",
                "movement": "static",
                "eyeline": "left",
                "covers_lines": [1, 2],
                "intent": "Hold on Alice.",
            }
        ],
    }


class TestParseLlmShotlist:
    def test_clean_json_parses(self) -> None:
        result = parse_llm_shotlist(json.dumps(valid_payload()))
        assert isinstance(result, SceneShotList)
        assert result.scene_ordinal == 1
        assert result.shots[0].size == "cu"

    def test_json_in_markdown_fences_parses(self) -> None:
        raw = "```json\n" + json.dumps(valid_payload(), indent=2) + "\n```"
        result = parse_llm_shotlist(raw)
        assert isinstance(result, SceneShotList)

    def test_json_in_bare_fences_parses(self) -> None:
        raw = "```\n" + json.dumps(valid_payload()) + "\n```"
        result = parse_llm_shotlist(raw)
        assert isinstance(result, SceneShotList)

    def test_prose_wrapped_json_parses(self) -> None:
        raw = (
            "Here is the shot list you asked for:\n\n"
            + json.dumps(valid_payload())
            + "\n\nLet me know if you'd like adjustments!"
        )
        result = parse_llm_shotlist(raw)
        assert isinstance(result, SceneShotList)

    def test_invalid_enum_returns_schema_failure(self) -> None:
        payload = valid_payload()
        payload["shots"][0]["size"] = "extreme-wide"
        result = parse_llm_shotlist(json.dumps(payload))
        assert isinstance(result, SchemaFailure)
        assert result.errors
        assert any("size" in err for err in result.errors)

    def test_unparseable_text_returns_schema_failure(self) -> None:
        result = parse_llm_shotlist("I could not produce a shot list, sorry.")
        assert isinstance(result, SchemaFailure)
        assert result.errors

    def test_malformed_json_returns_schema_failure(self) -> None:
        result = parse_llm_shotlist('{"scene_ordinal": 1, "shots": [')
        assert isinstance(result, SchemaFailure)
        assert result.errors

    def test_never_raises_on_junk(self) -> None:
        for raw in ["", "```json\n```", "{}", "[1, 2, 3]", "null"]:
            result = parse_llm_shotlist(raw)
            assert isinstance(result, (SceneShotList, SchemaFailure))
