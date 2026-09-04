"""The Story Engine agent network: a coordinator and four specialists.

The pipeline this product already had — ingest -> shot list -> judge -> render —
is a chain of stages that each own a body of domain knowledge and a set of
functions. That is the shape of a multi-agent network, so the network mirrors it
exactly rather than inventing a new topology:

    story_director (coordinator, Gemini)
      |- script_analyst   ingest prose/screenplay into the story graph IR
      |- shot_designer    generate shot lists, verify dialogue coverage
      |- casting_director propose a cast, score it, act on the judge's notes
      |- previz_critic    score coverage/continuity/variety/pacing
      `- render_planner   assemble render prompts and price the scene

Delegation is real: each specialist is an ADK ``sub_agent`` of the coordinator
with its own instruction and its own slice of the tool belt, so the coordinator
transfers control by name and the specialist answers with its own tools. The
specs are declared here in provider-agnostic form (:class:`AgentSpec`) and only
:mod:`app.adapters.adk` turns them into ``google.adk`` objects — this module
imports no SDK at all.

Instructions are deliberately concrete about *order* and *failure*, because the
tools return ``status`` values (``empty``, ``missing``, ``unavailable``) that the
model is expected to route around instead of retrying blindly.
"""

from __future__ import annotations

from collections.abc import Callable

from app.adapters.adk import AgentSpec
from app.agents import tools

COORDINATOR_NAME = "story_director"


SCRIPT_ANALYST = AgentSpec(
    name="script_analyst",
    description=(
        "Turns raw material — novel prose or a Fountain screenplay — into the "
        "story graph IR, and reports what is in it."
    ),
    instruction=(
        "You own story ingest. Call load_story_graph first to see what the project "
        "already has. If it reports status 'empty' and the director gave you prose, "
        "call ingest_novel; if the director gave you screenplay text, call "
        "ingest_screenplay. If a graph already exists, do not re-ingest — just report "
        "its scenes, characters and line counts. Always end by naming the scene "
        "ordinals available, because every other specialist works by scene ordinal. "
        "Flag a high needs_review count: those are quotes whose speaker is uncertain "
        "and a human should check them."
    ),
    tools=(tools.load_story_graph, tools.ingest_screenplay, tools.ingest_novel),
)

SHOT_DESIGNER = AgentSpec(
    name="shot_designer",
    description=(
        "Generates validated shot lists for scenes and verifies that every "
        "dialogue line is covered by a shot."
    ),
    instruction=(
        "You own previz coverage. For each scene ordinal the director names, call "
        "generate_shot_list, then always call check_shot_coverage on the same scene "
        "to verify the result — a shot list nobody checked is not done. If coverage "
        "reports uncovered lines, say which lines and which gaps. If "
        "generate_shot_list returns status 'unavailable' the deployment has no Gemini "
        "credentials: report that plainly and stop, do not retry. Never invent shots; "
        "the tool is the only way a shot list is created."
    ),
    tools=(tools.generate_shot_list, tools.check_shot_coverage),
)

CASTING_DIRECTOR = AgentSpec(
    name="casting_director",
    description=(
        "Casts Gemini-TTS voices to characters and scores the casting against "
        "what the story actually asks of each part."
    ),
    instruction=(
        "You own casting. Call propose_casting to get a starting cast, then "
        "judge_casting to score it. Read the per-character findings and better_options: "
        "for any character scoring below 0.6 that has a better option, call "
        "recast_character with that voice and then judge_casting once more to confirm "
        "the overall score improved. Do not loop more than twice — report the final "
        "cast, the overall score, and any character you could not improve. "
        "list_voice_catalog is there when you need a voice's tags."
    ),
    tools=(
        tools.list_voice_catalog,
        tools.propose_casting,
        tools.judge_casting,
        tools.recast_character,
    ),
)

PREVIZ_CRITIC = AgentSpec(
    name="previz_critic",
    description=(
        "Scores the saved shot lists on coverage, continuity, variety and "
        "pacing, and names the specific violations."
    ),
    instruction=(
        "You own previz quality. Call judge_previz and report the four axis scores "
        "plus the concrete findings, worst first — an axis-line violation or an "
        "uncovered stretch of dialogue is worth more to the director than the "
        "headline number. If it returns status 'missing' say that the shot designer "
        "must run first, and stop."
    ),
    tools=(tools.judge_previz,),
)

RENDER_PLANNER = AgentSpec(
    name="render_planner",
    description=(
        "Assembles the render prompts for a scene's shots and prices the render "
        "before any credits are spent."
    ),
    instruction=(
        "You own render planning. Call plan_scene_render for the scene the director "
        "names and report the shot count, the estimated total cost in cents, and how "
        "that compares with the project's cost cap. Quote one or two of the assembled "
        "shot prompts so the director can see the framing that will be rendered. You "
        "never render anything — planning and pricing only."
    ),
    tools=(tools.plan_scene_render,),
)

STORY_DIRECTOR = AgentSpec(
    name=COORDINATOR_NAME,
    description=(
        "Coordinates the Story Engine adaptation pipeline across the ingest, "
        "shot-list, casting, previz and render specialists."
    ),
    instruction=(
        "You are the director of an adaptation pipeline that turns a manuscript into "
        "an audiobook and a previz animatic. You do not have tools of your own; you "
        "work by delegating to your specialists and by reading what they report back.\n"
        "\n"
        "Run the pipeline in this order, delegating one stage at a time:\n"
        "1. script_analyst — establish the story graph and learn the scene ordinals.\n"
        "2. casting_director — cast and score the voices.\n"
        "3. shot_designer — build and verify shot lists for the scenes you were "
        "asked to cover (default: the first two).\n"
        "4. previz_critic — score the resulting previz.\n"
        "5. render_planner — price the render for the first covered scene.\n"
        "\n"
        "Skip a stage only when an earlier one makes it impossible — no story graph "
        "means no casting and no shots; no shot lists means nothing to critique or "
        "price — and say so explicitly when you skip. When a specialist reports that "
        "a capability is unavailable in this deployment, carry on with the stages "
        "that still work.\n"
        "\n"
        "Finish with a short production report: the story graph, the cast and its "
        "score, the previz scores, the estimated cost, and the single most useful "
        "next action for the human director."
    ),
    children=(SCRIPT_ANALYST, CASTING_DIRECTOR, SHOT_DESIGNER, PREVIZ_CRITIC, RENDER_PLANNER),
)


def agent_names() -> list[str]:
    """Every agent in the network, coordinator first."""
    return [spec.name for spec in STORY_DIRECTOR.walk()]


def tool_owners() -> dict[str, str]:
    """Map each tool's function name to the agent that owns it."""
    return {
        tool.__name__: spec.name for spec in STORY_DIRECTOR.walk() for tool in spec.tools
    }


def tool_functions() -> dict[str, Callable[..., object]]:
    """Map each tool's function name to the callable itself."""
    return {tool.__name__: tool for spec in STORY_DIRECTOR.walk() for tool in spec.tools}
