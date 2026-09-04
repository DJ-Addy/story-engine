"""An in-memory stand-in for the slice of ``google-adk`` the seam actually uses.

The architectural law is that ``google.adk`` is imported in exactly one module,
lazily, and never in a test: the suite must collect and pass on a machine with
the SDK uninstalled and no Google credentials. :class:`~app.adapters.adk.AdkApi`
is the seam that makes that possible - six symbols in one value - and this
module supplies fakes for all six.

The fakes are deliberately not mocks. ``FakeRunner`` walks a scripted plan of
``(agent, tool, args)`` steps, and for each one it *looks the tool up on the
agent object the real* :meth:`AdkAgentRuntime._build` *constructed and calls it*.
So a test that runs a plan exercises the real spec tree, the real tool
functions, the real repository and the real judges end to end; only the model's
choice of what to call next is scripted, because that is the one part that would
cost credits.

It also mimics the two ADK behaviours the event translation depends on:

* delegation is a call to the built-in ``transfer_to_agent`` tool, emitted by
  the coordinator whenever control moves to a different specialist;
* a tool that raises propagates out of ``run_async`` rather than being folded
  into a response, which is what the "surface, never swallow" path must handle.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any

from app.adapters.adk import AdkApi
from app.agents.events import TRANSFER_TOOL


@dataclass
class Step:
    """One scripted turn: who acts, which of their tools they call, with what."""

    agent: str
    tool: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    text: str | None = None


@dataclass
class Recorder:
    """What the fake SDK saw, so tests can assert on construction and wiring."""

    agents: list[Any] = field(default_factory=list)
    root: Any = None
    prompts: list[str] = field(default_factory=list)
    sessions: list[dict[str, Any]] = field(default_factory=list)
    runners: list[dict[str, Any]] = field(default_factory=list)

    def agent(self, name: str) -> Any:
        for built in self.agents:
            if built.name == name:
                return built
        raise AssertionError(f"no agent named {name} was built")


class FakePart:
    def __init__(self, text: str | None = None) -> None:
        self.text = text


class FakeContent:
    def __init__(self, role: str | None = None, parts: list[Any] | None = None) -> None:
        self.role = role
        self.parts = list(parts or [])


class FakeCall:
    def __init__(self, name: str, args: dict[str, Any]) -> None:
        self.name = name
        self.args = args


class FakeResponse:
    def __init__(self, name: str, response: Any) -> None:
        self.name = name
        self.response = response


class FakeEvent:
    """Shaped like an ADK ``Event``: the three helpers the seam calls, no more."""

    def __init__(
        self,
        author: str,
        *,
        text: str | None = None,
        calls: list[FakeCall] | None = None,
        responses: list[FakeResponse] | None = None,
        final: bool = False,
    ) -> None:
        self.author = author
        self.content = (
            FakeContent(role="model", parts=[FakePart(text)]) if text is not None else None
        )
        self._calls = list(calls or [])
        self._responses = list(responses or [])
        self._final = final

    def get_function_calls(self) -> list[FakeCall]:
        return self._calls

    def get_function_responses(self) -> list[FakeResponse]:
        return self._responses

    def is_final_response(self) -> bool:
        return self._final


class FakeFunctionTool:
    """ADK wraps a callable; the callable stays reachable, as it does for real."""

    def __init__(self, func: Any) -> None:
        self.func = func
        self.name = getattr(func, "__name__", str(func))


class FakeLlmAgent:
    """Everything :meth:`AdkAgentRuntime._build` passes, kept for assertions."""

    def __init__(
        self,
        *,
        name: str,
        model: str,
        description: str,
        instruction: str,
        tools: list[FakeFunctionTool],
        sub_agents: list[FakeLlmAgent],
    ) -> None:
        self.name = name
        self.model = model
        self.description = description
        self.instruction = instruction
        self.tools = list(tools)
        self.sub_agents = list(sub_agents)

    def find(self, name: str) -> FakeLlmAgent | None:
        if self.name == name:
            return self
        for child in self.sub_agents:
            hit = child.find(name)
            if hit is not None:
                return hit
        return None

    def tool(self, name: str) -> FakeFunctionTool:
        for tool in self.tools:
            if tool.name == name:
                return tool
        raise AssertionError(f"agent {self.name} was not given a tool named {name}")


class FakeSession:
    def __init__(self, session_id: str, state: dict[str, Any]) -> None:
        self.id = session_id
        self.state = state


def build_fake_adk(
    steps: list[Step],
    *,
    opening: str = "Reading the brief.",
    report: str = "Production report: story graph established, cast scored, previz judged.",
) -> tuple[AdkApi, Recorder]:
    """An :class:`AdkApi` of fakes that will play ``steps``, plus its recorder."""
    recorder = Recorder()

    class _LlmAgent(FakeLlmAgent):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            recorder.agents.append(self)

    class _SessionService:
        async def create_session(
            self,
            *,
            app_name: str,
            user_id: str,
            session_id: str | None = None,
            state: dict[str, Any] | None = None,
        ) -> FakeSession:
            recorder.sessions.append(
                {
                    "app_name": app_name,
                    "user_id": user_id,
                    "session_id": session_id,
                    "state": dict(state or {}),
                }
            )
            return FakeSession(session_id or "fake-session", dict(state or {}))

    class _Runner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any) -> None:
            self.app_name = app_name
            self.agent = agent
            self.session_service = session_service
            recorder.root = agent
            recorder.runners.append({"app_name": app_name, "agent": agent})

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any):
            recorder.prompts.append(
                "".join(part.text or "" for part in getattr(new_message, "parts", []))
            )
            root = self.agent
            yield FakeEvent(root.name, text=opening)

            speaking = root.name
            for step in steps:
                if step.agent != speaking:
                    # ADK's own delegation: the coordinator calls the built-in
                    # transfer tool, and the specialist acknowledges it.
                    yield FakeEvent(
                        root.name,
                        calls=[FakeCall(TRANSFER_TOOL, {"agent_name": step.agent})],
                    )
                    yield FakeEvent(
                        step.agent,
                        responses=[FakeResponse(TRANSFER_TOOL, {"result": None})],
                    )
                    speaking = step.agent
                if step.text is not None:
                    yield FakeEvent(step.agent, text=step.text)
                if step.tool is None:
                    continue

                agent = root.find(step.agent)
                assert agent is not None, f"the network has no agent {step.agent}"
                tool = agent.tool(step.tool)
                yield FakeEvent(
                    step.agent, calls=[FakeCall(step.tool, dict(step.args))]
                )
                # The real function, with the real RunContext bound around it.
                result = tool.func(**step.args)
                if inspect.isawaitable(result):
                    result = await result
                yield FakeEvent(
                    step.agent, responses=[FakeResponse(step.tool, result)]
                )

            yield FakeEvent(root.name, text=report, final=True)

    api = AdkApi(
        LlmAgent=_LlmAgent,
        Runner=_Runner,
        InMemorySessionService=_SessionService,
        FunctionTool=FakeFunctionTool,
        Content=FakeContent,
        Part=FakePart,
    )
    return api, recorder
