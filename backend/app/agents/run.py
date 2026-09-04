"""Driving one run of the Story Engine agent network, step by visible step.

This is the layer between the HTTP surface and the ADK seam. It owns the three
things a run needs that neither of them should:

* **the bound context** — :class:`~app.agents.context.RunContext` is set for the
  whole run so every tool the model calls resolves the same repository, project
  and LLM, and is unbound again on the way out even if the client disconnects;
* **the narrative** — raw ADK events are translated into the wire events of
  :mod:`app.agents.events` and the run's own summary (who was delegated to,
  which pipeline functions actually executed, what the director concluded) is
  accumulated as they go by;
* **failure** — a specialist whose tool raises must be *seen*. The exception is
  caught exactly once, logged, and re-emitted as a ``run_failed`` step followed
  by a ``run_completed`` with ``status="failed"``. Nothing is swallowed and the
  transcript up to the failure survives, which is the whole point of streaming.

The generator is deliberately the only shape here: the non-streaming endpoint is
just ``[event async for event in run.stream(prompt)]``, so both endpoints report
the same run rather than two code paths that can drift.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from uuid import uuid4

from app.adapters.adk import AdkAgentRuntime, AgentSpec, spec_tool_names
from app.agents.context import RunContext, use_context
from app.agents.events import (
    AgentEvent,
    DelegationEvent,
    MessageEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    translate,
)

logger = logging.getLogger(__name__)


def default_brief(title: str, max_scenes: int) -> str:
    """The one-click brief: run the whole pipeline over the first few scenes.

    The coordinator's instruction already encodes the stage order, so the brief
    only has to name the project and the scope. Callers who want something
    narrower ("just re-cast the villain") send their own.
    """
    scenes = "scene" if max_scenes == 1 else f"first {max_scenes} scenes"
    return (
        f'Produce an adaptation pass on the project "{title}". Establish the story '
        f"graph, cast the voices and score the casting, build and verify shot lists "
        f"for the {scenes}, score the resulting previz, and price the render for the "
        "first covered scene. Finish with the production report."
    )


class AgentRun:
    """One run of an :class:`AgentSpec` tree, as a stream of readable steps.

    Construction is free of I/O: nothing resolves credentials, imports an SDK or
    touches the network until :meth:`stream` is iterated.
    """

    def __init__(
        self,
        *,
        runtime: AdkAgentRuntime,
        spec: AgentSpec,
        context: RunContext,
        user_id: str,
        run_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.spec = spec
        self.context = context
        self.user_id = user_id
        self.run_id = run_id or uuid4().hex
        self.session_id = session_id or self.run_id

        self.status: str = "running"
        self.final_text: str | None = None
        self.error: str | None = None
        self.delegations: list[str] = []
        self.event_count = 0

    @property
    def tool_calls(self) -> list[str]:
        """The pipeline functions that actually ran, in order.

        Read from the :class:`RunContext` rather than from the event stream:
        every tool notes itself on entry, so a tool that raised half way through
        still appears — which is exactly the one you want named in a failure.
        """
        return list(self.context.tool_calls)

    def summary(self) -> RunCompletedEvent:
        """The closing event, also the summary the non-streaming endpoint returns."""
        return RunCompletedEvent(
            run_id=self.run_id,
            status="failed" if self.status == "failed" else "completed",
            final_text=self.final_text,
            delegations=list(self.delegations),
            tool_calls=self.tool_calls,
            event_count=self.event_count,
        )

    async def stream(self, prompt: str) -> AsyncIterator[AgentEvent]:
        """Run the network on ``prompt``, yielding steps as ADK emits them."""
        yield RunStartedEvent(
            run_id=self.run_id,
            project_id=self.context.project_id,
            coordinator=self.spec.name,
            agents=[child.name for child in self.spec.walk()],
            tools=spec_tool_names(self.spec.walk()),
            model=self.runtime.model,
        )

        try:
            # Bound for the whole run, including the tasks ADK spawns inside the
            # runner: contextvars are copied into child tasks at creation, and
            # every task here is created while this block is active.
            with use_context(self.context):
                async for adk_event in self.runtime.run(
                    self.spec,
                    prompt,
                    user_id=self.user_id,
                    session_id=self.session_id,
                ):
                    for event in translate(adk_event, self.run_id):
                        self._observe(event)
                        yield event
        except Exception as exc:  # a failed specialist is reported, never hidden
            self.status = "failed"
            self.error = f"{type(exc).__name__}: {exc}"
            logger.exception("agent run %s failed", self.run_id)
            yield RunFailedEvent(
                run_id=self.run_id, error=type(exc).__name__, detail=str(exc)
            )
        else:
            self.status = "completed"

        yield self.summary()

    def _observe(self, event: AgentEvent) -> None:
        """Accumulate the run's summary from the events passing through."""
        self.event_count += 1
        if isinstance(event, DelegationEvent):
            self.delegations.append(event.to_agent)
        elif isinstance(event, MessageEvent) and event.final and event.text.strip():
            # The last final message is the coordinator's production report;
            # specialists' finals are superseded as the run continues.
            self.final_text = event.text

    async def collect(self, prompt: str) -> list[AgentEvent]:
        """The whole run as a list — the non-streaming endpoint's transcript."""
        return [event async for event in self.stream(prompt)]
