"""A rejected statement must raise, never look like an empty success.

The bug these cover shipped and reached a live cluster: every DDL and INSERT
failed with ``UNKNOWN_DATABASE``, and the API reported ``reachable: true`` with
``written: 3, failed: 0``. ``mcp-clickhouse`` logs the exception on its own side
and returns an ordinary result — ``isError`` unset — whose body is the error
text, which the parser accepted as the acknowledgement DDL legitimately returns.

A silent analytics failure is worse than a loud one: the dashboard is simply
empty, which reads as "no data yet" rather than "nothing has ever been written".
"""

from __future__ import annotations

import json

import pytest

from app.analytics.mcp_client import (
    ClickHouseUnavailable,
    QueryResult,
    parse_tool_result,
)


class _Block:
    def __init__(self, text: str) -> None:
        self.text = text


class _Result:
    """The shape FastMCP delivers: content blocks plus optional structured data."""

    def __init__(self, text: str | None = None, *, structured=None, is_error: bool = False):
        self.content = [_Block(text)] if text is not None else []
        self.structuredContent = structured
        self.isError = is_error


# The real message, copied from the Cloud Run logs of the failed deployment.
_REAL_FAILURE = (
    "Error executing query 4b8d8060: Received ClickHouse exception, code: 81, "
    "server response: Code: 81. DB::Exception: Database story_engine does not "
    "exist. (UNKNOWN_DATABASE) (version 26.2.1.641 (official build))"
)


# --------------------------------------------------------------------------- #
# Failures must raise
# --------------------------------------------------------------------------- #
def test_bare_error_string_raises_rather_than_reading_as_a_ddl_ack() -> None:
    """The exact regression: non-JSON error text was treated as success."""
    with pytest.raises(ClickHouseUnavailable) as excinfo:
        parse_tool_result(_Result(_REAL_FAILURE))
    assert "UNKNOWN_DATABASE" in str(excinfo.value)


def test_status_error_dict_raises() -> None:
    payload = json.dumps({"status": "error", "message": _REAL_FAILURE})
    with pytest.raises(ClickHouseUnavailable) as excinfo:
        parse_tool_result(_Result(payload))
    assert "story_engine" in str(excinfo.value)


def test_legacy_error_key_still_raises() -> None:
    payload = json.dumps({"error": "read-only user"})
    with pytest.raises(ClickHouseUnavailable):
        parse_tool_result(_Result(payload))


def test_is_error_flag_raises() -> None:
    with pytest.raises(ClickHouseUnavailable):
        parse_tool_result(_Result("anything", is_error=True))


def test_error_reported_through_structured_content_raises() -> None:
    result = _Result(structured={"result": {"status": "error", "message": _REAL_FAILURE}})
    with pytest.raises(ClickHouseUnavailable):
        parse_tool_result(result)


@pytest.mark.parametrize(
    "text",
    [
        "error running query: something broke",
        "Failed to connect to ClickHouse: timeout",
        "Code: 81. DB::Exception: Database story_engine does not exist.",
    ],
)
def test_known_failure_phrasings_raise(text: str) -> None:
    with pytest.raises(ClickHouseUnavailable):
        parse_tool_result(_Result(text))


# --------------------------------------------------------------------------- #
# Successes must not raise — the acknowledgement path still has to work
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", ["", "   ", "Ok.", "query executed successfully"])
def test_acknowledgements_are_still_empty_successes(text: str) -> None:
    """DDL and INSERT return prose, not JSON. That must stay a success."""
    assert parse_tool_result(_Result(text)) == QueryResult()


def test_rows_parse_normally() -> None:
    payload = json.dumps({"columns": ["voice", "score"], "rows": [["Kore", 0.61]]})
    result = parse_tool_result(_Result(payload))
    assert result.columns == ["voice", "score"]
    assert result.rows == [["Kore", 0.61]]
    assert result.dicts() == [{"voice": "Kore", "score": 0.61}]


def test_empty_result_set_is_not_an_error() -> None:
    """Zero rows is a legitimate answer and must not be confused with failure."""
    payload = json.dumps({"columns": ["voice"], "rows": []})
    assert parse_tool_result(_Result(payload)) == QueryResult(columns=["voice"], rows=[])


def test_row_of_dicts_shape_still_supported() -> None:
    payload = json.dumps([{"voice": "Kore", "score": 0.61}])
    result = parse_tool_result(_Result(payload))
    assert result.columns == ["voice", "score"]
    assert result.rows == [["Kore", 0.61]]


def test_error_word_inside_a_data_row_is_not_treated_as_a_failure() -> None:
    """Only the envelope decides failure — payload *data* may say anything."""
    payload = json.dumps({"columns": ["msg"], "rows": [["DB::Exception in a log line"]]})
    result = parse_tool_result(_Result(payload))
    assert result.rows == [["DB::Exception in a log line"]]


# --------------------------------------------------------------------------- #
# The connection database must stay bootstrappable
# --------------------------------------------------------------------------- #
def test_child_server_connects_to_a_database_that_already_exists(monkeypatch) -> None:
    """Forwarding Story Engine's own database made the schema uncreatable.

    The server connects against CLICKHOUSE_DATABASE, so naming a database that
    does not exist yet fails the connection — and with it the CREATE DATABASE
    that would have fixed it. Our statements are fully qualified, so the
    connection only needs somewhere real to land.
    """
    from app.analytics.settings import AnalyticsSettings

    monkeypatch.setenv("CLICKHOUSE_HOST", "example.clickhouse.cloud")
    monkeypatch.delenv("CLICKHOUSE_CONNECT_DATABASE", raising=False)
    monkeypatch.setenv("STORY_ENGINE_CLICKHOUSE_DATABASE", "story_engine")

    settings = AnalyticsSettings.from_env()

    assert settings.database == "story_engine"
    assert settings.server_env["CLICKHOUSE_DATABASE"] == "default"


def test_connection_database_is_overridable(monkeypatch) -> None:
    """A cluster without `default` still needs a way in."""
    from app.analytics.settings import AnalyticsSettings

    monkeypatch.setenv("CLICKHOUSE_HOST", "example.clickhouse.cloud")
    monkeypatch.setenv("CLICKHOUSE_CONNECT_DATABASE", "landing")

    assert AnalyticsSettings.from_env().server_env["CLICKHOUSE_DATABASE"] == "landing"
