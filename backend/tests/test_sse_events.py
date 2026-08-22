"""Tests for the SSE event contract (PRD §5.5): golden strings and round-trips."""

import json

from app.workers.events import ChildFailedEvent, DoneEvent, ProgressEvent, to_sse


def data_json(sse: str) -> dict:
    """Extract and parse the data payload from a serialized SSE frame."""
    lines = sse.split("\n")
    assert lines[1].startswith("data: ")
    return json.loads(lines[1][len("data: ") :])


class TestProgressEvent:
    def test_golden_string(self):
        event = ProgressEvent(job_id="job-1", progress=0.5, stage="tts", completed=2, total=4)
        assert to_sse(event) == (
            "event: progress\n"
            'data: {"job_id":"job-1","progress":0.5,"stage":"tts","completed":2,"total":4}\n'
            "\n"
        )

    def test_json_round_trip(self):
        event = ProgressEvent(job_id="j", progress=0.25, stage="mix", completed=1, total=8)
        assert data_json(to_sse(event)) == event.model_dump()


class TestChildFailedEvent:
    def test_golden_string(self):
        event = ChildFailedEvent(job_id="job-1", child_id="abc", line_ordinal=3, error="boom")
        assert to_sse(event) == (
            "event: child_failed\n"
            'data: {"job_id":"job-1","child_id":"abc","line_ordinal":3,"error":"boom"}\n'
            "\n"
        )

    def test_none_line_ordinal_serializes_as_null(self):
        event = ChildFailedEvent(job_id="j", child_id="c", line_ordinal=None, error="e")
        assert '"line_ordinal":null' in to_sse(event)

    def test_json_round_trip(self):
        event = ChildFailedEvent(job_id="j", child_id="c", line_ordinal=None, error="e")
        assert data_json(to_sse(event)) == event.model_dump()


class TestDoneEvent:
    def test_golden_string(self):
        event = DoneEvent(
            job_id="job-1", state="partial", cost_cents=42, artifacts=["a.wav", "b.wav"]
        )
        assert to_sse(event) == (
            "event: done\n"
            'data: {"job_id":"job-1","state":"partial","cost_cents":42,'
            '"artifacts":["a.wav","b.wav"]}\n'
            "\n"
        )

    def test_json_round_trip(self):
        event = DoneEvent(job_id="j", state="succeeded", cost_cents=0, artifacts=[])
        assert data_json(to_sse(event)) == event.model_dump()


class TestFraming:
    def test_trailing_double_newline_on_every_event_type(self):
        events = [
            ProgressEvent(job_id="j", progress=1.0, stage="done", completed=4, total=4),
            ChildFailedEvent(job_id="j", child_id="c", line_ordinal=1, error="x"),
            DoneEvent(job_id="j", state="failed", cost_cents=3, artifacts=[]),
        ]
        for event in events:
            sse = to_sse(event)
            assert sse.endswith("\n\n")
            assert not sse.endswith("\n\n\n")

    def test_json_is_compact(self):
        event = DoneEvent(job_id="j", state="succeeded", cost_cents=1, artifacts=["x"])
        raw = to_sse(event).split("\n")[1][len("data: ") :]
        assert raw == json.dumps(json.loads(raw), separators=(",", ":"))
