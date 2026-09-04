"""The wire contract for a live agent run: what the demo actually watches.

:class:`~app.adapters.adk.AdkEvent` is the seam's flat view of one ADK event —
who spoke, what they said, what they called. That is the right shape for the
adapter and the wrong shape for a viewer: a single ADK event can carry a
sentence *and* a tool call, and the one call that matters most to a multi-agent
demo (``transfer_to_agent``, ADK's built-in delegation tool) looks like any
other function call inside it.

So this module fans one :class:`AdkEvent` out into the steps a human reads:

    message      an agent said something
    delegation   the coordinator handed control to a named specialist
    tool_call    a specialist invoked one of the real pipeline functions
    tool_result  that function answered, with its ``status``

plus ``run_started`` / ``run_completed`` / ``run_failed`` bookends. Framing is
identical to :mod:`app.workers.events` — named event, compact JSON, blank-line
terminator — because the frontend already speaks that dialect.

The payloads must survive ``json.dumps``: a tool result is whatever a pipeline
function returned, so every value is coerced to something serialisable rather
than risking a mid-stream ``TypeError`` that would truncate the response with a
200 already on the wire.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

from app.adapters.adk import AdkEvent

# ADK's built-in delegation tool. A coordinator transfers control to a
# ``sub_agent`` by calling it with the target's name, so intercepting it here is
# what turns an opaque function call into a visible
# "story_director -> casting_director" step.
TRANSFER_TOOL = "transfer_to_agent"

# The argument that names the target agent in a transfer call.
_TRANSFER_ARG = "agent_name"


class RunStartedEvent(BaseModel):
    """The network as it was assembled, before the first token."""

    run_id: str
    project_id: str
    coordinator: str
    agents: list[str]
    tools: list[str]
    model: str


class MessageEvent(BaseModel):
    """Prose from one agent. ``final`` marks the end of the coordinator's turn."""

    run_id: str
    agent: str
    text: str
    final: bool = False


class DelegationEvent(BaseModel):
    """One agent handing control to another — the multi-agent step itself."""

    run_id: str
    from_agent: str
    to_agent: str


class ToolCallEvent(BaseModel):
    """A specialist invoking one of the real pipeline functions."""

    run_id: str
    agent: str
    tool: str
    args: dict[str, Any]


class ToolResultEvent(BaseModel):
    """What that function returned. ``status`` is the tools' own contract key."""

    run_id: str
    agent: str
    tool: str
    status: str | None = None
    result: dict[str, Any]


class RunFailedEvent(BaseModel):
    """The run raised. Surfaced as a step, never swallowed into silence."""

    run_id: str
    error: str
    detail: str


class RunCompletedEvent(BaseModel):
    """Always last, on both the happy and the failed path."""

    run_id: str
    status: Literal["completed", "failed"]
    final_text: str | None = None
    delegations: list[str] = []
    tool_calls: list[str] = []
    event_count: int = 0


AgentEvent = (
    RunStartedEvent
    | MessageEvent
    | DelegationEvent
    | ToolCallEvent
    | ToolResultEvent
    | RunFailedEvent
    | RunCompletedEvent
)


_EVENT_NAMES: dict[type, str] = {
    RunStartedEvent: "run_started",
    MessageEvent: "message",
    DelegationEvent: "delegation",
    ToolCallEvent: "tool_call",
    ToolResultEvent: "tool_result",
    RunFailedEvent: "run_failed",
    RunCompletedEvent: "run_completed",
}


def event_name(event: AgentEvent) -> str:
    """The SSE event name for one event model."""
    return _EVENT_NAMES[type(event)]


def to_payload(event: AgentEvent) -> dict[str, Any]:
    """The event as a plain dict, tagged with its ``type``.

    The non-streaming endpoint returns a list of these, so a transcript
    collected from one endpoint is byte-identical to the frames streamed by the
    other apart from the SSE framing.
    """
    return {"type": event_name(event), **event.model_dump()}


def to_sse(event: AgentEvent) -> str:
    """Serialize to a wire-ready SSE frame."""
    data = json.dumps(event.model_dump(), separators=(",", ":"), default=repr)
    return f"event: {event_name(event)}\ndata: {data}\n\n"


def _jsonable(value: Any) -> Any:
    """``value`` if it survives ``json.dumps``, else its repr.

    Tool results come from the pipeline, not from a schema, so this is the last
    line of defence for a stream that has already sent its status line.
    """
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)
    return value


def _as_args(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(k): _jsonable(v) for k, v in value.items()}


def _as_result(value: object) -> dict[str, Any]:
    """Normalise a tool response to a dict, wrapping scalars under ``result``."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return {"result": _jsonable(value)}


def _status_of(result: dict[str, Any]) -> str | None:
    status = result.get("status")
    return status if isinstance(status, str) else None


def translate(event: AdkEvent, run_id: str) -> list[AgentEvent]:
    """Fan one normalised ADK event out into the steps a viewer reads.

    Order within an event follows how it reads aloud: the agent speaks, then
    delegates or calls a tool, then the tool answers. An ADK event usually
    carries only one of those, so the list is normally a single item.
    """
    out: list[AgentEvent] = []

    if event.text:
        out.append(
            MessageEvent(
                run_id=run_id, agent=event.author, text=event.text, final=event.final
            )
        )

    for name, args in event.function_calls:
        if name == TRANSFER_TOOL:
            target = args.get(_TRANSFER_ARG) if isinstance(args, dict) else None
            out.append(
                DelegationEvent(
                    run_id=run_id,
                    from_agent=event.author,
                    to_agent=str(target) if target else "unknown",
                )
            )
        else:
            out.append(
                ToolCallEvent(
                    run_id=run_id, agent=event.author, tool=name, args=_as_args(args)
                )
            )

    for name, response in event.function_responses:
        # The transfer "result" is ADK bookkeeping with nothing in it; the
        # delegation event above is the meaningful half.
        if name == TRANSFER_TOOL:
            continue
        result = _as_result(response)
        out.append(
            ToolResultEvent(
                run_id=run_id,
                agent=event.author,
                tool=name,
                status=_status_of(result),
                result=result,
            )
        )

    return out
