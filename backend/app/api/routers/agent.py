"""The agent network over HTTP: what it is, and watching it work.

Three endpoints, and the middle one is the reason the other two exist.

``GET /agent/network``
    The deployment's answer to "is there really a multi-agent network in here?"
    - the coordinator, its five specialists, each one's instruction and its
    slice of the tool belt, plus whether ``google-adk`` is importable and a
    Google Cloud project is configured. It answers with no credentials, no SDK
    and no project, because a status endpoint that only works when everything
    is working reports nothing worth knowing. Unauthenticated for the same
    reason ``/health`` is: it describes the deployment, not anyone's data.

``POST /projects/{id}/agent/run/stream``
    One run of the network as Server-Sent Events, in the framing
    :mod:`app.workers.events` already established. The coordinator delegating
    to ``casting_director``, that specialist calling ``judge_casting``, and the
    score coming back are three separate frames that land as they happen - so
    the multi-agent behaviour is *visible* rather than inferred from a
    paragraph produced ninety seconds later.

``POST /projects/{id}/agent/run``
    The same run, collected. For clients that would rather have one JSON body
    than a stream; the transcript it returns is the same event list, tagged
    with ``type`` instead of SSE-framed.

Two deliberate shapes:

* **Fail fast on configuration, stream through on execution.** A missing SDK or
  an unconfigured project is a 503 with the env vars to set, decided *before*
  the response starts. A failure once the run is under way cannot change a
  status line that has already been sent, so it arrives as a ``run_failed``
  frame with the transcript up to that point intact.
* **Everything the model must not choose is a dependency.** Repository, project,
  LLM and ADK runtime are injected, so tests substitute a fake ADK and a fake
  LLM and every line of routing, context binding and event translation below
  still runs for real. Nothing here imports ``google.adk``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.adapters.adk import AdkAgentRuntime, runtime_info, spec_tool_names
from app.adapters.base import LLMProvider, TerminalProviderError
from app.agents import network
from app.agents.context import RunContext
from app.agents.events import to_payload, to_sse
from app.agents.run import AgentRun, default_brief
from app.api.deps import get_current_user, get_llm, get_owned_project, get_repo
from app.api.repo import ProjectRecord, Repository, UserRecord

# Project-scoped runs; the network description is deployment-scoped and lives on
# ``meta_router`` below, so it needs neither a project nor a token.
router = APIRouter(prefix="/projects/{project_id}/agent", tags=["agent"])
meta_router = APIRouter(prefix="/agent", tags=["agent"])

# ``no-cache`` stops a browser replaying a stale run; ``X-Accel-Buffering: no``
# stops an nginx / Cloud Run style proxy holding frames back until the response
# closes, which would collapse the whole point of streaming into one late burst.
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

MAX_SCENES = 20


# --------------------------------------------------------------------------- #
# Dependencies
# --------------------------------------------------------------------------- #
def get_agent_runtime() -> AdkAgentRuntime:
    """The ADK runtime for this deployment.

    Constructing one resolves nothing - no import, no credentials, no socket -
    so this is safe to build per request and safe on a machine with no Google
    Cloud setup at all. Tests override it with a runtime over a fake ``AdkApi``.
    """
    return AdkAgentRuntime()


def get_agent_llm() -> LLMProvider | None:
    """Gemini for the tools that need it, or ``None`` when none is configured.

    The optional-LLM contract the rest of the pipeline already keeps: ingest,
    casting and both judges degrade to their deterministic heuristics, and only
    ``generate_shot_list`` genuinely needs a provider - it reports
    ``status: unavailable`` rather than failing, which the shot designer's
    instruction tells it to route around. So a deployment with the ADK but no
    Gemini still runs the network and still produces a report; it just has a
    hole in it that the transcript names.
    """
    try:
        return get_llm()
    except TerminalProviderError:
        return None


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class AgentToolOut(BaseModel):
    """One tool, and which specialist is allowed to call it."""

    name: str
    owner: str
    summary: str


class AgentDescriptionOut(BaseModel):
    """One agent exactly as the network declares it."""

    name: str
    description: str
    instruction: str
    tools: list[str]
    delegates_to: list[str]


class AgentNetworkOut(BaseModel):
    """The network, plus the runtime it would execute on."""

    sdk: str
    installed: bool
    project_configured: bool
    available: bool
    detail: str | None = None
    model: str
    location: str
    vertex_backend: bool
    coordinator: str
    agents: list[AgentDescriptionOut]
    tools: list[AgentToolOut]


class AgentRunRequest(BaseModel):
    """What to ask the director. Everything has a sane default."""

    brief: str | None = Field(
        default=None,
        description="Instruction for the coordinator; omit for a full pipeline pass.",
        max_length=8000,
    )
    max_scenes: int = Field(default=2, ge=1, le=MAX_SCENES)
    session_id: str | None = Field(default=None, max_length=200)


class AgentRunOut(BaseModel):
    """One completed run: the summary, and every step that produced it."""

    run_id: str
    project_id: str
    status: Literal["completed", "failed"]
    model: str
    coordinator: str
    prompt: str
    final_text: str | None = None
    delegations: list[str]
    tool_calls: list[str]
    error: str | None = None
    events: list[dict[str, Any]]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _summary_line(func: object) -> str:
    """The first line of a tool's docstring - the sentence the model reads."""
    doc = (getattr(func, "__doc__", None) or "").strip()
    return doc.splitlines()[0] if doc else ""


def _unavailable_detail(installed: bool, project_configured: bool) -> str | None:
    """Why the network cannot run here, phrased as the thing to go and do."""
    if not installed:
        return (
            "google-adk is not installed in this deployment; run `pip install -e .` "
            "in backend/ to pull the Google Cloud Agent Builder (ADK) dependencies"
        )
    if not project_configured:
        return (
            "no Google Cloud project configured for the agent network; set "
            "GOOGLE_CLOUD_PROJECT and GOOGLE_APPLICATION_CREDENTIALS"
        )
    return None


def _require_runtime(runtime: AdkAgentRuntime) -> None:
    """503 with an actionable reason when the network cannot run here.

    Checked before the response starts: an SSE body cannot retract its status
    line, so configuration problems must be settled while a status code is
    still ours to choose.
    """
    info = runtime_info(runtime)
    detail = _unavailable_detail(info.installed, info.project_configured)
    if detail is not None:
        raise HTTPException(status_code=503, detail=detail)


def _build_run(
    body: AgentRunRequest,
    project: ProjectRecord,
    user: UserRecord,
    repo: Repository,
    runtime: AdkAgentRuntime,
    llm: LLMProvider | None,
) -> tuple[AgentRun, str]:
    """Bind one run to this project, this user and this deployment's providers."""
    context = RunContext(
        repo=repo,
        project_id=project.id,
        grammar_profile=project.grammar_profile,
        llm=llm,
        max_scenes=body.max_scenes,
    )
    run = AgentRun(
        runtime=runtime,
        spec=network.STORY_DIRECTOR,
        context=context,
        # ADK sessions are per user, and row-level ownership is already settled
        # by get_owned_project, so the session key is the real account.
        user_id=user.id,
        session_id=body.session_id,
    )
    return run, body.brief or default_brief(project.title, body.max_scenes)


# --------------------------------------------------------------------------- #
# The network itself
# --------------------------------------------------------------------------- #
@meta_router.get("/network", response_model=AgentNetworkOut)
def describe_network(
    runtime: AdkAgentRuntime = Depends(get_agent_runtime),
) -> AgentNetworkOut:
    """The agent network, and whether this deployment can run it.

    Never raises: with nothing installed and nothing configured it still returns
    the full topology with ``available: false`` and the reason, which is what
    lets the API boot and serve on a machine with no Google credentials.
    """
    info = runtime_info(runtime)
    specs = network.STORY_DIRECTOR.walk()
    owners = network.tool_owners()
    functions = network.tool_functions()

    info.agents = [spec.name for spec in specs]
    info.tools = spec_tool_names(specs)

    detail = _unavailable_detail(info.installed, info.project_configured)
    return AgentNetworkOut(
        sdk=info.sdk,
        installed=info.installed,
        project_configured=info.project_configured,
        available=detail is None,
        detail=detail,
        model=info.model,
        location=info.location,
        vertex_backend=info.vertex_backend,
        coordinator=network.COORDINATOR_NAME,
        agents=[
            AgentDescriptionOut(
                name=spec.name,
                description=spec.description,
                instruction=spec.instruction,
                tools=[tool.__name__ for tool in spec.tools],
                delegates_to=[child.name for child in spec.children],
            )
            for spec in specs
        ],
        tools=[
            AgentToolOut(
                name=name,
                owner=owners.get(name, ""),
                summary=_summary_line(functions.get(name)),
            )
            for name in info.tools
        ],
    )


# --------------------------------------------------------------------------- #
# Running it
# --------------------------------------------------------------------------- #
@router.post(
    "/run/stream",
    # Without this /docs advertises an application/json body for a response that
    # is only ever an event stream.
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server-Sent Events: run_started, message, delegation, "
            "tool_call, tool_result, run_failed, run_completed.",
            "content": {"text/event-stream": {}},
        },
        503: {"description": "The ADK or a Google Cloud project is not configured."},
    },
)
async def run_agent_stream(
    body: AgentRunRequest,
    project: ProjectRecord = Depends(get_owned_project),
    user: UserRecord = Depends(get_current_user),
    repo: Repository = Depends(get_repo),
    runtime: AdkAgentRuntime = Depends(get_agent_runtime),
    llm: LLMProvider | None = Depends(get_agent_llm),
) -> StreamingResponse:
    """Run the network, streaming every delegation, tool call and result live."""
    _require_runtime(runtime)
    run, prompt = _build_run(body, project, user, repo, runtime, llm)

    async def frames():
        async for event in run.stream(prompt):
            yield to_sse(event)

    return StreamingResponse(
        frames(), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.post("/run", response_model=AgentRunOut)
async def run_agent(
    body: AgentRunRequest,
    project: ProjectRecord = Depends(get_owned_project),
    user: UserRecord = Depends(get_current_user),
    repo: Repository = Depends(get_repo),
    runtime: AdkAgentRuntime = Depends(get_agent_runtime),
    llm: LLMProvider | None = Depends(get_agent_llm),
) -> AgentRunOut:
    """Run the network and return the whole transcript in one body.

    A run that fails answers 200 with ``status: "failed"``, the error and the
    steps that got that far - the transcript is the useful part of a failure,
    and throwing it away to raise a 5xx would be the swallowing this layer
    exists to prevent.
    """
    _require_runtime(runtime)
    run, prompt = _build_run(body, project, user, repo, runtime, llm)
    events = await run.collect(prompt)

    return AgentRunOut(
        run_id=run.run_id,
        project_id=project.id,
        status="failed" if run.status == "failed" else "completed",
        model=runtime.model,
        coordinator=network.COORDINATOR_NAME,
        prompt=prompt,
        final_text=run.final_text,
        delegations=run.delegations,
        tool_calls=run.tool_calls,
        error=run.error,
        events=[to_payload(event) for event in events],
    )
