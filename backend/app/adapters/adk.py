"""Google Cloud Agent Builder seam: the Agent Development Kit (ADK) runtime.

"Google Cloud Agent Builder" is the umbrella product (rebranded in 2026 as the
Gemini Enterprise Agent Platform) whose *code-first* surface is the **Agent
Development Kit**, shipped on PyPI as ``google-adk``. The Agentic Cinema rules
name ``google-adk`` first in the list of accepted Google Cloud SDKs and require
it to be "imported and actually called" at runtime, so this module is the one
place in the codebase that imports it — the same architectural law every other
provider SDK obeys (``google.auth`` in :mod:`app.adapters.google_auth`, and
nothing else anywhere).

What lives here is only the *seam*: turning a provider-agnostic
:class:`AgentSpec` tree into real ``google.adk`` ``LlmAgent`` objects, driving
them with a real ``Runner`` + ``InMemorySessionService``, and normalising the
ADK ``Event`` stream into :class:`AdkEvent`. The agents themselves — what they
are called, what they are told to do, and which pipeline functions they can
call — are declared in :mod:`app.agents`, which never imports ``google.adk``.

ARCHITECTURAL LAW (PRD): no provider SDK is imported outside app/adapters/, and
construction never touches credentials or the network. ``google.adk`` is
imported *lazily* inside :func:`load_adk`, so this module imports cleanly with
nothing installed and offline test collection stays green. Tests inject a fake
:class:`AdkApi` through ``api_loader`` and never import the real SDK.

Environment (Vertex AI backend, which is what the hackathon requires):

* ``GOOGLE_CLOUD_PROJECT``       — billing/quota project (also selects the ADK path)
* ``GOOGLE_APPLICATION_CREDENTIALS`` — ADC service-account key (or gcloud login)
* ``GOOGLE_CLOUD_LOCATION``      — Vertex region, defaults to ``us-central1``
* ``GOOGLE_GENAI_USE_VERTEXAI``  — forced to ``1`` so google-genai talks to
  Vertex AI rather than the AI Studio API-key endpoint
* ``STORY_ENGINE_AGENT_MODEL``   — Gemini model for every agent in the network,
  falling back to ``GOOGLE_GEMINI_MODEL`` and then to the adapter default

Docs: https://google.github.io/adk-docs/ — verified against google-adk 2.8.0.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field

from app.adapters.base import TerminalProviderError

# Agents are cheap, high-frequency planners: Flash is the right default and the
# same family the rest of the pipeline already prices in app.adapters.gemini.
_DEFAULT_MODEL = "gemini-3.8-flash"

# Vertex AI agent traffic is region-pinned (unlike the Gemini "global" endpoint
# app.adapters.gemini uses), so the ADK path keeps its own location default.
# Gemini is served from "global", not from a region: a `generateContent` against
# us-central1 answers 404 NOT_FOUND for every Gemini model, which is what took
# the agent network down. `app.adapters.gemini` already defaults this way, and
# the ADK runs the same models, so it defaults the same way. Veo is the opposite
# - genuinely region-pinned - which is why it now reads GOOGLE_VEO_LOCATION and
# no longer shares this setting.
_DEFAULT_LOCATION = "global"

_DEFAULT_APP_NAME = "story-engine"


# --------------------------------------------------------------------------- #
# Provider-agnostic description of an agent network
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class AgentSpec:
    """One agent in the network, described without reference to any SDK.

    ``tools`` are ordinary Python callables — the real pipeline functions from
    :mod:`app.agents.tools`. ADK derives each tool's name, description and JSON
    schema from the function's name, docstring and type hints, so the callables
    are the contract; nothing here re-declares them.

    ``children`` become ADK ``sub_agents``: a coordinator delegates to them by
    name, which is what makes this a multi-agent network rather than one agent
    with a big tool belt.
    """

    name: str
    description: str
    instruction: str
    tools: tuple[Callable[..., object], ...] = ()
    children: tuple["AgentSpec", ...] = ()

    def walk(self) -> list["AgentSpec"]:
        """This spec followed by every descendant, depth-first."""
        out = [self]
        for child in self.children:
            out.extend(child.walk())
        return out


@dataclass(frozen=True)
class AdkEvent:
    """One normalised event from an ADK run.

    ADK's ``Event`` is a rich pydantic model tied to the SDK; the rest of the
    app only needs "who spoke, what did they say, what did they call, and was
    that the end of a turn", so the seam narrows it to that.
    """

    author: str
    text: str | None = None
    function_calls: tuple[tuple[str, dict], ...] = ()
    function_responses: tuple[tuple[str, object], ...] = ()
    final: bool = False


@dataclass(frozen=True)
class AdkApi:
    """The handful of ``google.adk`` / ``google.genai`` symbols this seam uses.

    Bundling them into one value is what makes the runtime testable: tests pass
    an ``api_loader`` returning fakes and every line of construction, wiring and
    event normalisation below still runs for real.
    """

    LlmAgent: type
    Runner: type
    InMemorySessionService: type
    FunctionTool: type
    Content: type
    Part: type


def load_adk() -> AdkApi:
    """Import the Agent Development Kit, lazily and with a useful failure.

    Raises :class:`TerminalProviderError` (never ``ImportError``) when
    ``google-adk`` is absent, so callers can degrade rather than crash and the
    module still imports on a machine with nothing installed.
    """
    try:
        from google.adk.agents import LlmAgent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.adk.tools import FunctionTool
        from google.genai import types
    except ImportError as exc:  # ModuleNotFoundError is a subclass
        raise TerminalProviderError(
            "google-adk is not installed; run `pip install -e .` in backend/ to "
            "pull the Google Cloud Agent Builder (ADK) dependencies"
        ) from exc
    return AdkApi(
        LlmAgent=LlmAgent,
        Runner=Runner,
        InMemorySessionService=InMemorySessionService,
        FunctionTool=FunctionTool,
        Content=types.Content,
        Part=types.Part,
    )


def adk_installed(api_loader: Callable[[], AdkApi] = load_adk) -> bool:
    """Whether the ADK can be imported right now. Never raises."""
    try:
        api_loader()
    except TerminalProviderError:
        return False
    return True


def _text_of(content: object) -> str | None:
    """Concatenate the text parts of an ADK event's content, if any."""
    parts = getattr(content, "parts", None) or []
    text = "".join(getattr(part, "text", None) or "" for part in parts)
    return text or None


def _as_dict(value: object) -> dict:
    if isinstance(value, dict):
        return value
    return {}


class AdkAgentRuntime:
    """Builds and runs an :class:`AgentSpec` tree on the real ADK.

    Construction resolves nothing: no credentials, no imports, no network — so
    an instance can be made on a machine with no Google Cloud setup and only
    :meth:`run` fails, with a message naming the env vars to set.
    """

    name = "google-adk"

    def __init__(
        self,
        model: str | None = None,
        *,
        app_name: str = _DEFAULT_APP_NAME,
        api_loader: Callable[[], AdkApi] = load_adk,
    ) -> None:
        self._model = (
            model
            or os.environ.get("STORY_ENGINE_AGENT_MODEL")
            or os.environ.get("GOOGLE_GEMINI_MODEL")
            or _DEFAULT_MODEL
        )
        self._app_name = app_name
        self._api_loader = api_loader

    @property
    def model(self) -> str:
        return self._model

    @property
    def api_loader(self) -> Callable[[], AdkApi]:
        """The loader this runtime was built with.

        Public so :func:`runtime_info` can ask *this* runtime whether its SDK is
        importable instead of re-checking the ambient one — the difference
        between a truthful status endpoint and one that reports on a runtime
        nobody is using.
        """
        return self._api_loader

    def available(self) -> bool:
        """True when the ADK is importable *and* a project is configured."""
        return bool(os.environ.get("GOOGLE_CLOUD_PROJECT")) and adk_installed(self._api_loader)

    @staticmethod
    def configure_vertex_env() -> None:
        """Point google-genai (which ADK uses for Gemini) at Vertex AI.

        ADK reads these from the environment when an agent names its model as a
        string. Only defaults are filled in — an operator who has set them wins.
        """
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "1")
        os.environ.setdefault(
            "GOOGLE_CLOUD_LOCATION",
            os.environ.get("GOOGLE_GEMINI_LOCATION") or _DEFAULT_LOCATION,
        )

    def build(self, spec: AgentSpec, api: AdkApi | None = None) -> object:
        """Turn a spec tree into a tree of real ADK ``LlmAgent`` objects."""
        api = api or self._api_loader()
        return self._build(spec, api)

    def _build(self, spec: AgentSpec, api: AdkApi) -> object:
        return api.LlmAgent(
            name=spec.name,
            model=self._model,
            description=spec.description,
            instruction=spec.instruction,
            tools=[api.FunctionTool(tool) for tool in spec.tools],
            sub_agents=[self._build(child, api) for child in spec.children],
        )

    async def run(
        self,
        spec: AgentSpec,
        prompt: str,
        *,
        user_id: str,
        session_id: str | None = None,
        state: dict | None = None,
    ) -> AsyncIterator[AdkEvent]:
        """Run the network on one prompt, yielding normalised events as they land.

        The generator is what makes the demo watchable: every delegation, tool
        call and tool result surfaces the moment ADK emits it, instead of one
        opaque answer at the end.
        """
        if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
            raise TerminalProviderError(
                "no Google Cloud project configured for the agent network; set "
                "GOOGLE_CLOUD_PROJECT and GOOGLE_APPLICATION_CREDENTIALS"
            )
        api = self._api_loader()
        self.configure_vertex_env()

        root = self._build(spec, api)
        session_service = api.InMemorySessionService()
        runner = api.Runner(
            app_name=self._app_name,
            agent=root,
            session_service=session_service,
        )
        session = await session_service.create_session(
            app_name=self._app_name,
            user_id=user_id,
            session_id=session_id,
            state=state or {},
        )
        message = api.Content(role="user", parts=[api.Part(text=prompt)])

        async for event in runner.run_async(
            user_id=user_id,
            session_id=getattr(session, "id", session_id),
            new_message=message,
        ):
            yield self._normalize(event)

    @staticmethod
    def _normalize(event: object) -> AdkEvent:
        """Flatten one ADK ``Event`` into an :class:`AdkEvent`.

        ``get_function_calls`` / ``get_function_responses`` / ``is_final_response``
        are the SDK's own helpers; going through them rather than picking apart
        ``content.parts`` keeps this correct across ADK versions.
        """
        calls = tuple(
            (getattr(call, "name", "") or "", _as_dict(getattr(call, "args", None)))
            for call in (event.get_function_calls() or ())
        )
        responses = tuple(
            (getattr(resp, "name", "") or "", getattr(resp, "response", None))
            for resp in (event.get_function_responses() or ())
        )
        return AdkEvent(
            author=getattr(event, "author", "") or "unknown",
            text=_text_of(getattr(event, "content", None)),
            function_calls=calls,
            function_responses=responses,
            final=bool(event.is_final_response()),
        )


@dataclass
class AdkRuntimeInfo:
    """What the API reports about the agent runtime, for the hosted demo."""

    sdk: str = "google-adk"
    installed: bool = False
    project_configured: bool = False
    model: str = _DEFAULT_MODEL
    location: str = _DEFAULT_LOCATION
    vertex_backend: bool = True
    agents: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


def runtime_info(
    runtime: AdkAgentRuntime | None = None,
    api_loader: Callable[[], AdkApi] = load_adk,
) -> AdkRuntimeInfo:
    """Describe the ADK runtime without importing or calling anything heavy."""
    if runtime is None:
        runtime = AdkAgentRuntime(api_loader=api_loader)
    else:
        # A caller who handed us a runtime is asking about that one, so its
        # loader decides "installed" — otherwise an injected runtime would be
        # reported against whatever happens to be importable in this process.
        api_loader = runtime.api_loader
    return AdkRuntimeInfo(
        installed=adk_installed(api_loader),
        project_configured=bool(os.environ.get("GOOGLE_CLOUD_PROJECT")),
        model=runtime.model,
        location=os.environ.get("GOOGLE_CLOUD_LOCATION") or _DEFAULT_LOCATION,
    )


def spec_tool_names(specs: Sequence[AgentSpec]) -> list[str]:
    """Every distinct tool name across a set of specs, in declaration order."""
    names: list[str] = []
    for spec in specs:
        for tool in spec.tools:
            name = getattr(tool, "__name__", str(tool))
            if name not in names:
                names.append(name)
    return names
