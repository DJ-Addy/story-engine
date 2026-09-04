"""The agent run's wire contract: translation from ADK events, and SSE framing.

Golden strings and round-trips, in the style of tests/test_sse_events.py - the
frontend parses these frames, so the framing is part of the API and changing it
should have to change a test.
"""

import json

import pytest

from app.adapters.adk import AdkEvent
from app.agents.events import (
    TRANSFER_TOOL,
    DelegationEvent,
    MessageEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    ToolCallEvent,
    ToolResultEvent,
    event_name,
    to_payload,
    to_sse,
    translate,
)

RUN = "run-1"


def data_json(sse: str) -> dict:
    lines = sse.split("\n")
    assert lines[1].startswith("data: ")
    return json.loads(lines[1][len("data: ") :])


class TestFraming:
    def test_delegation_golden_string(self):
        event = DelegationEvent(run_id="r1", from_agent="story_director", to_agent="previz_critic")
        assert to_sse(event) == (
            "event: delegation\n"
            'data: {"run_id":"r1","from_agent":"story_director",'
            '"to_agent":"previz_critic"}\n'
            "\n"
        )

    def test_every_event_type_has_a_name_and_frames_cleanly(self):
        events = [
            RunStartedEvent(
                run_id=RUN,
                project_id="p1",
                coordinator="story_director",
                agents=["story_director"],
                tools=["judge_previz"],
                model="gemini-3.8-flash",
            ),
            MessageEvent(run_id=RUN, agent="story_director", text="Working."),
            DelegationEvent(run_id=RUN, from_agent="story_director", to_agent="previz_critic"),
            ToolCallEvent(run_id=RUN, agent="previz_critic", tool="judge_previz", args={}),
            ToolResultEvent(
                run_id=RUN,
                agent="previz_critic",
                tool="judge_previz",
                status="ok",
                result={"status": "ok"},
            ),
            RunFailedEvent(run_id=RUN, error="RuntimeError", detail="boom"),
            RunCompletedEvent(run_id=RUN, status="completed"),
        ]
        names = [event_name(event) for event in events]
        assert names == [
            "run_started",
            "message",
            "delegation",
            "tool_call",
            "tool_result",
            "run_failed",
            "run_completed",
        ]
        for event in events:
            frame = to_sse(event)
            assert frame.startswith("event: ")
            assert frame.endswith("\n\n")
            assert not frame.endswith("\n\n\n")
            assert data_json(frame) == event.model_dump()

    def test_json_is_compact(self):
        raw = to_sse(MessageEvent(run_id=RUN, agent="a", text="t")).split("\n")[1][len("data: ") :]
        assert raw == json.dumps(json.loads(raw), separators=(",", ":"))

    def test_payload_is_the_frame_body_tagged_with_its_type(self):
        event = MessageEvent(run_id=RUN, agent="a", text="t", final=True)
        assert to_payload(event) == {"type": "message", **data_json(to_sse(event))}


class TestTranslate:
    def test_text_becomes_a_message(self):
        events = translate(
            AdkEvent(author="story_director", text="Reading the brief.", final=True), RUN
        )
        assert len(events) == 1
        assert isinstance(events[0], MessageEvent)
        assert (events[0].agent, events[0].text, events[0].final) == (
            "story_director",
            "Reading the brief.",
            True,
        )

    def test_the_transfer_tool_becomes_a_delegation(self):
        """The one call that makes a multi-agent run legible."""
        events = translate(
            AdkEvent(
                author="story_director",
                function_calls=((TRANSFER_TOOL, {"agent_name": "casting_director"}),),
            ),
            RUN,
        )
        assert events == [
            DelegationEvent(
                run_id=RUN, from_agent="story_director", to_agent="casting_director"
            )
        ]

    def test_a_transfer_without_a_target_is_still_reported(self):
        events = translate(
            AdkEvent(author="story_director", function_calls=((TRANSFER_TOOL, {}),)), RUN
        )
        assert events[0].to_agent == "unknown"

    def test_the_transfer_response_is_dropped_as_bookkeeping(self):
        events = translate(
            AdkEvent(
                author="casting_director",
                function_responses=((TRANSFER_TOOL, {"result": None}),),
            ),
            RUN,
        )
        assert events == []

    def test_a_tool_call_and_its_result_carry_the_status(self):
        call = translate(
            AdkEvent(
                author="shot_designer",
                function_calls=(("generate_shot_list", {"scene_ordinal": 1}),),
            ),
            RUN,
        )[0]
        assert isinstance(call, ToolCallEvent)
        assert (call.agent, call.tool, call.args) == (
            "shot_designer",
            "generate_shot_list",
            {"scene_ordinal": 1},
        )

        result = translate(
            AdkEvent(
                author="shot_designer",
                function_responses=(
                    ("generate_shot_list", {"status": "unavailable", "reason": "no Gemini"}),
                ),
            ),
            RUN,
        )[0]
        assert isinstance(result, ToolResultEvent)
        assert result.status == "unavailable"
        assert result.result["reason"] == "no Gemini"

    def test_one_adk_event_fans_out_in_reading_order(self):
        events = translate(
            AdkEvent(
                author="casting_director",
                text="Scoring the cast.",
                function_calls=(("judge_casting", {}),),
                function_responses=(("judge_casting", {"status": "ok"}),),
            ),
            RUN,
        )
        assert [type(event).__name__ for event in events] == [
            "MessageEvent",
            "ToolCallEvent",
            "ToolResultEvent",
        ]

    def test_an_empty_event_produces_nothing(self):
        assert translate(AdkEvent(author="story_director"), RUN) == []

    def test_a_scalar_result_is_wrapped_and_has_no_status(self):
        result = translate(
            AdkEvent(author="previz_critic", function_responses=(("judge_previz", 0.82),)), RUN
        )[0]
        assert result.result == {"result": 0.82}
        assert result.status is None

    def test_a_non_string_status_is_not_reported_as_one(self):
        result = translate(
            AdkEvent(author="previz_critic", function_responses=(("judge_previz", {"status": 7}),)),
            RUN,
        )[0]
        assert result.status is None
        assert result.result == {"status": 7}

    @pytest.mark.parametrize("args", [None, "not-a-dict", 7])
    def test_non_dict_call_args_degrade_to_empty(self, args):
        event = translate(
            AdkEvent(author="shot_designer", function_calls=(("check_shot_coverage", args),)), RUN
        )[0]
        assert event.args == {}

    def test_an_unserialisable_result_becomes_its_repr_rather_than_a_broken_stream(self):
        """A mid-stream TypeError would truncate a response that already sent a 200."""

        class Opaque:
            def __repr__(self) -> str:
                return "<Opaque>"

        result = translate(
            AdkEvent(
                author="render_planner",
                function_responses=(("plan_scene_render", {"handle": Opaque()}),),
            ),
            RUN,
        )[0]
        assert result.result == {"handle": "<Opaque>"}
        json.dumps(to_payload(result))  # must not raise
