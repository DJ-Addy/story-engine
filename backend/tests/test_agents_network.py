"""The agent network and the ADK seam, with the SDK fully stubbed.

No test here imports ``google.adk``, sets a credential or opens a socket: the
whole runtime is driven through the injected :class:`~app.adapters.adk.AdkApi`,
which is the seam that lets the suite collect and pass on a machine with
``google-adk`` uninstalled. What is *not* stubbed is everything else - the specs,
the tool functions, the repository and the judges all run for real.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
import typing

import pytest
from adk_fakes import FakeCall, FakeEvent, FakeResponse, Step, build_fake_adk

from app.adapters.adk import (
    AdkAgentRuntime,
    AgentSpec,
    adk_installed,
    runtime_info,
    spec_tool_names,
)
from app.adapters.base import TerminalProviderError
from app.agents import network
from app.agents.context import RunContext, use_context
from app.api.repo import InMemoryRepository
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize

SPECIALISTS = [
    "script_analyst",
    "casting_director",
    "shot_designer",
    "previz_critic",
    "render_planner",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The network's behaviour must not depend on the developer's environment.

    ``GOOGLE_GENAI_USE_VERTEXAI`` and ``GOOGLE_CLOUD_LOCATION`` are listed because
    ``configure_vertex_env`` writes them into ``os.environ`` during a run;
    monkeypatch records them here so they are rolled back afterwards instead of
    leaking into the rest of the session.
    """
    for var in (
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "GOOGLE_GEMINI_MODEL",
        "STORY_ENGINE_AGENT_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def seeded_repo(sample_fountain: str) -> tuple[InMemoryRepository, str]:
    """An in-memory repo holding one project with the sample screenplay ingested."""
    repo = InMemoryRepository()
    user = repo.create_user("director@example.com", "hash", "salt")
    project = repo.create_project(
        owner_id=user.id,
        title="Wager",
        grammar_profile="classical",
        validator_mode="strict",
        rights_attested=True,
    )
    repo.save_script(project.id, "fountain", normalize(parse_fountain(sample_fountain)))
    return repo, project.id


def run_context(repo: InMemoryRepository, project_id: str) -> RunContext:
    return RunContext(repo=repo, project_id=project_id, grammar_profile="classical")


class TestOfflineImport:
    def test_importing_the_agent_layer_does_not_import_the_sdk(self):
        """The law that keeps offline collection green."""
        import app.adapters.adk
        import app.agents.network
        import app.agents.run
        import app.api.routers.agent  # noqa: F401

        assert "google.adk" not in sys.modules

    def test_agent_modules_declare_no_sdk_import(self):
        """app/agents/ and the router are declaration only; only adapters may import.

        Checked over the parsed import statements rather than the source text,
        so the modules stay free to *talk* about the SDK in their docstrings -
        which is where the reasoning for this law is written down.
        """
        modules = [
            "app.agents.network",
            "app.agents.tools",
            "app.agents.run",
            "app.agents.events",
            "app.api.routers.agent",
        ]
        for name in modules:
            tree = ast.parse(inspect.getsource(sys.modules[name]))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.append(node.module or "")
            assert not [
                mod for mod in imported if mod.split(".")[0] == "google"
            ], f"{name} imports a Google SDK"

    def test_adk_installed_is_false_when_the_loader_cannot_import(self):
        def missing() -> None:
            raise TerminalProviderError("google-adk is not installed")

        assert adk_installed(missing) is False


class TestTopology:
    def test_coordinator_delegates_to_five_specialists(self):
        assert network.STORY_DIRECTOR.name == network.COORDINATOR_NAME == "story_director"
        assert [child.name for child in network.STORY_DIRECTOR.children] == SPECIALISTS

    def test_agent_names_lists_the_coordinator_first(self):
        assert network.agent_names() == ["story_director", *SPECIALISTS]

    def test_the_coordinator_has_no_tools_of_its_own(self):
        """It is a router, not a doer - the whole point of the sub-agent split."""
        assert network.STORY_DIRECTOR.tools == ()

    def test_specialists_are_leaves(self):
        for child in network.STORY_DIRECTOR.children:
            assert child.children == ()
            assert child.tools, f"{child.name} has no tools"

    def test_every_tool_belongs_to_exactly_one_specialist(self):
        owners = network.tool_owners()
        all_tools = [
            tool for spec in network.STORY_DIRECTOR.walk() for tool in spec.tools
        ]
        assert len(all_tools) == len(owners)
        assert set(owners.values()) == set(SPECIALISTS)

    def test_spec_tool_names_dedupes_and_keeps_declaration_order(self):
        spec = AgentSpec(name="a", description="d", instruction="i")
        shared = network.tool_functions()["load_story_graph"]
        one = AgentSpec(name="one", description="d", instruction="i", tools=(shared,))
        two = AgentSpec(name="two", description="d", instruction="i", tools=(shared,))
        assert spec_tool_names([spec, one, two]) == ["load_story_graph"]

    def test_walk_is_depth_first_from_the_root(self):
        assert network.STORY_DIRECTOR.walk()[0] is network.STORY_DIRECTOR
        assert len(network.STORY_DIRECTOR.walk()) == 6


class TestToolContract:
    """ADK derives each tool's JSON schema from its signature and docstring."""

    def test_every_tool_has_a_docstring_the_model_can_read(self):
        for name, func in network.tool_functions().items():
            assert (func.__doc__ or "").strip(), f"{name} has no docstring"

    def test_tool_parameters_are_annotated_primitives_without_defaults(self):
        """The signature *is* the JSON schema ADK sends to Gemini."""
        for name, func in network.tool_functions().items():
            hints = typing.get_type_hints(func)
            signature = inspect.signature(func)
            for param in signature.parameters.values():
                assert hints.get(param.name) in (str, int), f"{name}.{param.name}"
                assert param.default is inspect.Parameter.empty, f"{name}.{param.name}"
            assert hints.get("return") is dict, f"{name} must return a dict"

    def test_tools_return_a_status_dict_rather_than_raising(self, seeded_repo):
        repo, project_id = seeded_repo
        with use_context(run_context(repo, project_id)):
            assert network.tool_functions()["load_story_graph"]()["status"] == "ok"
            # No shot list saved yet: a missing precondition is data, not an error.
            assert (
                network.tool_functions()["check_shot_coverage"](scene_ordinal=1)["status"]
                == "missing"
            )

    def test_a_tool_called_outside_a_run_says_so(self):
        with pytest.raises(RuntimeError, match="RunContext"):
            network.tool_functions()["load_story_graph"]()


class TestBuild:
    def test_the_spec_tree_becomes_an_agent_tree(self):
        api, _ = build_fake_adk([])
        runtime = AdkAgentRuntime(api_loader=lambda: api)
        root = runtime.build(network.STORY_DIRECTOR)

        assert root.name == "story_director"
        assert [child.name for child in root.sub_agents] == SPECIALISTS
        assert root.instruction == network.STORY_DIRECTOR.instruction

    def test_each_specialist_is_given_its_own_slice_of_the_tool_belt(self):
        api, _ = build_fake_adk([])
        root = AdkAgentRuntime(api_loader=lambda: api).build(network.STORY_DIRECTOR)

        by_name = {child.name: child for child in root.sub_agents}
        assert [tool.name for tool in by_name["previz_critic"].tools] == ["judge_previz"]
        assert [tool.name for tool in by_name["casting_director"].tools] == [
            "list_voice_catalog",
            "propose_casting",
            "judge_casting",
            "recast_character",
        ]

    def test_every_agent_gets_the_configured_model(self, monkeypatch):
        monkeypatch.setenv("STORY_ENGINE_AGENT_MODEL", "gemini-test-pro")
        api, recorder = build_fake_adk([])
        runtime = AdkAgentRuntime(api_loader=lambda: api)
        runtime.build(network.STORY_DIRECTOR)

        assert runtime.model == "gemini-test-pro"
        assert {agent.model for agent in recorder.agents} == {"gemini-test-pro"}

    def test_model_falls_back_to_the_gemini_model_then_the_default(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_GEMINI_MODEL", "gemini-shared")
        assert AdkAgentRuntime().model == "gemini-shared"
        monkeypatch.delenv("GOOGLE_GEMINI_MODEL")
        assert AdkAgentRuntime().model == "gemini-3.8-flash"


class TestRuntimeStatus:
    def test_unavailable_without_a_project_even_when_the_sdk_is_present(self):
        api, _ = build_fake_adk([])
        assert AdkAgentRuntime(api_loader=lambda: api).available() is False

    def test_available_with_both(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "story-engine-demo")
        api, _ = build_fake_adk([])
        assert AdkAgentRuntime(api_loader=lambda: api).available() is True

    def test_runtime_info_reports_on_the_runtime_it_was_given(self, monkeypatch):
        """Not on whatever happens to be importable in this process."""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "story-engine-demo")
        api, _ = build_fake_adk([])
        info = runtime_info(AdkAgentRuntime(api_loader=lambda: api))

        assert info.sdk == "google-adk"
        assert info.installed is True
        assert info.project_configured is True
        assert info.vertex_backend is True
        assert info.location == "us-central1"

    def test_runtime_info_degrades_without_the_sdk_or_a_project(self):
        def missing() -> None:
            raise TerminalProviderError("google-adk is not installed")

        info = runtime_info(api_loader=missing)
        assert (info.installed, info.project_configured) == (False, False)


class TestRun:
    async def test_refuses_to_run_without_a_project(self):
        api, _ = build_fake_adk([])
        runtime = AdkAgentRuntime(api_loader=lambda: api)
        with pytest.raises(TerminalProviderError, match="GOOGLE_CLOUD_PROJECT"):
            async for _ in runtime.run(network.STORY_DIRECTOR, "go", user_id="u"):
                pass

    async def test_drives_the_runner_and_normalises_its_events(
        self, monkeypatch, seeded_repo
    ):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "story-engine-demo")
        repo, project_id = seeded_repo
        api, recorder = build_fake_adk(
            [Step(agent="script_analyst", tool="load_story_graph")]
        )
        runtime = AdkAgentRuntime(api_loader=lambda: api)

        with use_context(run_context(repo, project_id)):
            events = [
                event
                async for event in runtime.run(
                    network.STORY_DIRECTOR,
                    "Adapt the project.",
                    user_id="user-1",
                    session_id="session-1",
                )
            ]

        assert recorder.prompts == ["Adapt the project."]
        assert recorder.sessions[0]["user_id"] == "user-1"
        assert recorder.sessions[0]["session_id"] == "session-1"
        assert recorder.runners[0]["app_name"] == "story-engine"

        authors = [event.author for event in events]
        assert authors[0] == "story_director"
        assert "script_analyst" in authors
        assert events[-1].final is True

        results = [
            response
            for event in events
            for _, response in event.function_responses
            if isinstance(response, dict) and "scene_count" in response
        ]
        assert results and results[0]["status"] == "ok"

    async def test_points_google_genai_at_vertex_ai(self, monkeypatch, seeded_repo):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "story-engine-demo")
        repo, project_id = seeded_repo
        api, _ = build_fake_adk([])

        with use_context(run_context(repo, project_id)):
            async for _ in AdkAgentRuntime(api_loader=lambda: api).run(
                network.STORY_DIRECTOR, "go", user_id="u"
            ):
                pass

        assert os.environ["GOOGLE_GENAI_USE_VERTEXAI"] == "1"
        assert os.environ["GOOGLE_CLOUD_LOCATION"] == "us-central1"

    async def test_a_tool_that_raises_propagates_out_of_the_run(
        self, monkeypatch, seeded_repo
    ):
        """The seam does not turn a broken specialist into a silent success."""
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "story-engine-demo")
        repo, project_id = seeded_repo

        def boom(project_id: str):
            raise RuntimeError("repository is down")

        monkeypatch.setattr(repo, "get_script", boom)
        api, _ = build_fake_adk([Step(agent="script_analyst", tool="load_story_graph")])

        with (
            pytest.raises(RuntimeError, match="repository is down"),
            use_context(run_context(repo, project_id)),
        ):
            async for _ in AdkAgentRuntime(api_loader=lambda: api).run(
                network.STORY_DIRECTOR, "go", user_id="u"
            ):
                pass


class TestNormalize:
    def test_flattens_text_calls_responses_and_finality(self):
        event = FakeEvent(
            "casting_director",
            text="Scoring the cast.",
            calls=[FakeCall("judge_casting", {"scene": 1})],
            responses=[FakeResponse("judge_casting", {"status": "ok"})],
            final=True,
        )
        normalized = AdkAgentRuntime._normalize(event)

        assert normalized.author == "casting_director"
        assert normalized.text == "Scoring the cast."
        assert normalized.function_calls == (("judge_casting", {"scene": 1}),)
        assert normalized.function_responses == (("judge_casting", {"status": "ok"}),)
        assert normalized.final is True

    def test_an_empty_event_normalises_to_no_text(self):
        normalized = AdkAgentRuntime._normalize(FakeEvent("story_director"))
        assert normalized.text is None
        assert normalized.function_calls == ()
        assert normalized.final is False

    def test_non_dict_call_args_do_not_break_normalisation(self):
        event = FakeEvent("shot_designer", calls=[FakeCall("generate_shot_list", None)])
        assert AdkAgentRuntime._normalize(event).function_calls == (
            ("generate_shot_list", {}),
        )
