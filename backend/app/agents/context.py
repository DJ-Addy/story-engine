"""Per-run context for the agent tools.

An ADK tool is a plain Python function whose parameters are filled in by the
model, so it cannot be handed a repository, a project id or an LLM provider —
the model would have to invent them. The standard answer is ambient state: the
coordinator binds a :class:`RunContext` for the duration of one run and the
tools read it.

A :class:`~contextvars.ContextVar` (not a module global) is what makes that
safe under the API's concurrency: every task started inside the run inherits a
copy of the context, and two requests in flight at once never see each other's
project. The ADK runner spawns tasks with ``asyncio``, which copies the current
context, so tool calls made deep inside the runner still resolve correctly.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from app.adapters.base import LLMProvider
from app.api.repo import Repository


@dataclass
class RunContext:
    """Everything the tools need that the model must not choose.

    ``llm`` is optional and stays optional: the shot-list generator genuinely
    requires one, but ingest, casting and both judges degrade to their
    deterministic heuristics, exactly as the rest of the pipeline does.

    ``casting`` is the run's working casting (character -> voice id). It is
    scratch state that ``propose_casting`` writes and ``judge_casting`` reads,
    which is how the two specialists cooperate without a shared database.
    """

    repo: Repository
    project_id: str
    grammar_profile: str = "classical"
    llm: LLMProvider | None = None
    casting: dict[str, str] = field(default_factory=dict)
    max_scenes: int = 2
    tool_calls: list[str] = field(default_factory=list)

    def note_call(self, name: str) -> None:
        self.tool_calls.append(name)


_current: ContextVar[RunContext | None] = ContextVar("story_engine_run_context", default=None)


@contextmanager
def use_context(context: RunContext) -> Iterator[RunContext]:
    """Bind ``context`` for the duration of the block, then restore."""
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


def current_context() -> RunContext:
    """The bound context, or a clear error if a tool ran outside a run."""
    context = _current.get()
    if context is None:
        raise RuntimeError(
            "no agent RunContext is bound; agent tools must be called inside "
            "app.agents.context.use_context(...)"
        )
    return context
