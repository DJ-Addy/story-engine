"""The agent network's tools — thin wrappers over the real Story Engine pipeline.

Every function here calls production code that already existed before the agent
layer: :mod:`app.ingest` for parse/normalize/novel-conversion,
:mod:`app.shotlist` for generation and coverage, :mod:`app.judge` for the voice
and animatic judges, :mod:`app.render.visual` for board prompts, and the real
provider adapters for the voice catalogue and cost estimates. There is no
parallel implementation and no simulation: if a tool says a scene has 87%
coverage, that number came out of ``app.shotlist.coverage``.

These are *tool functions* in the ADK sense, which constrains their shape:

* the signature is the schema — ADK derives each tool's JSON declaration from
  the parameter type hints, so arguments stay primitive and un-defaulted;
* the docstring is the description the model reads when deciding to call it;
* the return is always a JSON-serialisable ``dict`` with a ``status`` key, so a
  missing script or an unconfigured LLM is something the model can reason about
  and route around rather than an exception that kills the run.

Everything the model must not choose — repository, project id, LLM provider —
comes from the ambient :class:`~app.agents.context.RunContext`.
"""

from __future__ import annotations

from app.adapters.base import Voice
from app.agents.context import RunContext, current_context
from app.ingest.elements import StoryGraph
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize
from app.ingest.novel import novel_to_screenplay
from app.judge import judge_animatic, judge_voice_fit
from app.render.visual.prompts import board_prompt
from app.shotlist.coverage import coverage_gaps, uncovered_lines
from app.shotlist.generate import generate_shotlist
from app.shotlist.schema import SceneShotList

# Veo snaps clip lengths; 5s is its shortest supported take and the unit the
# previz timeline already assumes per shot.
_SHOT_SECONDS = 5


# --------------------------------------------------------------------------- #
# Shared helpers (not tools — never exposed to the model)
# --------------------------------------------------------------------------- #
def _graph(context: RunContext) -> StoryGraph | None:
    script = context.repo.get_script(context.project_id)
    return None if script is None else script.graph


def _no_script() -> dict:
    return {
        "status": "empty",
        "reason": "this project has no script yet; ingest a screenplay or a novel first",
    }


def _voice_catalog() -> list[Voice]:
    """The curated Gemini-TTS casting roster, straight from the TTS adapter.

    ``_CURATED_VOICES`` is static and offline — ``list_voices`` returns it
    without a request — so this costs nothing and needs no credentials.
    """
    from app.adapters.google_tts import _CURATED_VOICES

    return list(_CURATED_VOICES)


def _shotlists(context: RunContext, graph: StoryGraph) -> list[SceneShotList]:
    out: list[SceneShotList] = []
    for scene in graph.scenes:
        record = context.repo.get_shotlist(context.project_id, scene.ordinal)
        if record is not None:
            out.append(record.shotlist)
    return out


def _save_graph(context: RunContext, fountain_text: str) -> StoryGraph:
    """Re-parse Fountain through the canonical chain and persist the IR.

    Deliberately identical to what ``app.api.routers.scripts`` and
    ``app.api.routers.novel`` do, so an agent-ingested project is
    indistinguishable from a hand-uploaded one downstream.
    """
    graph = normalize(parse_fountain(fountain_text))
    context.repo.save_script(context.project_id, "fountain", graph)
    return graph


# --------------------------------------------------------------------------- #
# Story ingest agent
# --------------------------------------------------------------------------- #
def load_story_graph() -> dict:
    """Read the project's current story graph: scenes, characters and line counts.

    Call this first. It reports whether a script has been ingested at all, and
    gives the scene ordinals every other tool needs.
    """
    context = current_context()
    context.note_call("load_story_graph")
    graph = _graph(context)
    if graph is None:
        return _no_script()
    return {
        "status": "ok",
        "scene_count": len(graph.scenes),
        "character_count": len(graph.characters),
        "characters": [
            {"name": c.canonical_name, "line_count": c.line_count} for c in graph.characters
        ],
        "scenes": [
            {
                "ordinal": s.ordinal,
                "slugline": s.slugline,
                "location": s.location,
                "time_of_day": s.time_of_day,
                "line_count": len(s.lines),
                "dialogue_lines": sum(1 for line in s.lines if line.kind == "dialogue"),
            }
            for s in graph.scenes
        ],
    }


def ingest_screenplay(fountain_text: str) -> dict:
    """Parse Fountain screenplay text into the story graph and save it.

    Use for material that is already formatted as a screenplay. Replaces any
    script already on the project.
    """
    context = current_context()
    context.note_call("ingest_screenplay")
    if not fountain_text.strip():
        return {"status": "error", "reason": "fountain_text was empty"}
    graph = _save_graph(context, fountain_text)
    return {
        "status": "ok",
        "scene_count": len(graph.scenes),
        "character_count": len(graph.characters),
        "characters": [c.canonical_name for c in graph.characters],
    }


async def ingest_novel(prose_text: str) -> dict:
    """Convert novel prose to a screenplay, then into the story graph, and save it.

    Runs the full attribution pipeline: quotes are matched to speakers, and
    ``needs_review`` counts the ones that stayed ambiguous and want a human.
    """
    context = current_context()
    context.note_call("ingest_novel")
    if not prose_text.strip():
        return {"status": "error", "reason": "prose_text was empty"}
    result = await novel_to_screenplay(prose_text, llm=context.llm)
    graph = _save_graph(context, result.fountain_text)
    return {
        "status": "ok",
        "scene_count": len(graph.scenes),
        "character_count": len(graph.characters),
        "quotes": result.quotes,
        "attributed": result.attributed,
        "needs_review": result.needs_review,
        "characters": result.characters,
    }


# --------------------------------------------------------------------------- #
# Shot-list agent
# --------------------------------------------------------------------------- #
async def generate_shot_list(scene_ordinal: int) -> dict:
    """Generate and save a validated shot list for one scene, by scene ordinal.

    Uses Gemini with schema repair: an output that fails validation is fed back
    with its errors and retried. Requires an LLM; reports ``unavailable`` when
    none is configured rather than failing the run.
    """
    context = current_context()
    context.note_call("generate_shot_list")
    graph = _graph(context)
    if graph is None:
        return _no_script()
    if context.llm is None:
        return {
            "status": "unavailable",
            "reason": (
                "shot-list generation needs a Gemini provider; set GOOGLE_CLOUD_PROJECT "
                "and GOOGLE_APPLICATION_CREDENTIALS"
            ),
        }
    scene = next((s for s in graph.scenes if s.ordinal == scene_ordinal), None)
    if scene is None:
        return {"status": "error", "reason": f"no scene with ordinal {scene_ordinal}"}

    previous = context.repo.get_shotlist(context.project_id, scene_ordinal - 1)
    result = await generate_shotlist(
        scene,
        [c.canonical_name for c in graph.characters],
        context.grammar_profile,
        context.llm,
        prev_scene_last_shot=previous.shotlist.shots[-1] if previous is not None else None,
    )
    if result.status != "ok" or result.shot_list is None:
        return {
            "status": "failed",
            "scene_ordinal": scene_ordinal,
            "attempts": result.attempts,
            "errors": result.errors,
        }

    context.repo.save_shotlist(context.project_id, scene_ordinal, result.shot_list)
    return {
        "status": "ok",
        "scene_ordinal": scene_ordinal,
        "shot_count": len(result.shot_list.shots),
        "action_axis": result.shot_list.action_axis,
        "attempts": result.attempts,
        "uncovered_lines": result.uncovered,
        "shots": [
            {"ordinal": s.ordinal, "size": s.size, "subjects": s.subjects, "intent": s.intent}
            for s in result.shot_list.shots
        ],
    }


def check_shot_coverage(scene_ordinal: int) -> dict:
    """Check which dialogue lines the saved shot list for a scene fails to cover.

    Returns the uncovered line ordinals and the contiguous gaps between them, so
    the shot list can be repaired before anything is rendered.
    """
    context = current_context()
    context.note_call("check_shot_coverage")
    graph = _graph(context)
    if graph is None:
        return _no_script()
    scene = next((s for s in graph.scenes if s.ordinal == scene_ordinal), None)
    if scene is None:
        return {"status": "error", "reason": f"no scene with ordinal {scene_ordinal}"}
    record = context.repo.get_shotlist(context.project_id, scene_ordinal)
    if record is None:
        return {
            "status": "missing",
            "scene_ordinal": scene_ordinal,
            "reason": "no shot list saved for this scene yet",
        }

    dialogue = [line.ordinal for line in scene.lines if line.kind == "dialogue"]
    uncovered = uncovered_lines(record.shotlist, dialogue)
    covered = len(dialogue) - len(uncovered)
    return {
        "status": "ok",
        "scene_ordinal": scene_ordinal,
        "dialogue_lines": len(dialogue),
        "covered_lines": covered,
        "coverage": round(covered / len(dialogue), 3) if dialogue else 1.0,
        "uncovered_lines": uncovered,
        "gaps": [list(gap) for gap in coverage_gaps(record.shotlist, dialogue)],
    }


# --------------------------------------------------------------------------- #
# Casting agent
# --------------------------------------------------------------------------- #
def list_voice_catalog() -> dict:
    """List the Gemini-TTS voices available for casting, with their role tags."""
    current_context().note_call("list_voice_catalog")
    return {
        "status": "ok",
        "voices": [{"id": v.id, "name": v.name, "tags": list(v.tags)} for v in _voice_catalog()],
    }


def propose_casting() -> dict:
    """Propose a voice for every character, biggest speaking part first.

    A starting point for the casting judge to score, not a final answer:
    narration-heavy parts get narrator-tagged voices and the rest are dealt
    round-robin from the lead/character voices. Overwrites the run's working
    casting.
    """
    context = current_context()
    context.note_call("propose_casting")
    graph = _graph(context)
    if graph is None:
        return _no_script()

    narration_lines: dict[str, int] = {}
    total_lines: dict[str, int] = {}
    for scene in graph.scenes:
        for line in scene.lines:
            name = line.character_name
            if name is None:
                continue
            total_lines[name] = total_lines.get(name, 0) + 1
            if line.kind == "narration":
                narration_lines[name] = narration_lines.get(name, 0) + 1

    catalog = _voice_catalog()
    narrators = [v for v in catalog if "narrator" in v.tags]
    others = [v for v in catalog if "narrator" not in v.tags] or catalog

    def narrates(name: str) -> bool:
        total = total_lines.get(name, 0)
        return total > 0 and narration_lines.get(name, 0) / total >= 0.5

    ranked = sorted(
        graph.characters, key=lambda c: (-c.line_count, c.canonical_name)
    )
    casting: dict[str, str] = {}
    narrator_i = 0
    other_i = 0
    for character in ranked:
        name = character.canonical_name
        if narrates(name) and narrators:
            casting[name] = narrators[narrator_i % len(narrators)].id
            narrator_i += 1
        else:
            casting[name] = others[other_i % len(others)].id
            other_i += 1

    context.casting = casting
    return {"status": "ok", "casting": casting, "character_count": len(casting)}


async def judge_casting() -> dict:
    """Score the run's current casting against the story graph and suggest swaps.

    Runs the voice-fit judge: per-character fit scores, concrete findings (a
    soft voice on a shouting part, a narrator voice on a bit part) and better
    alternatives drawn from the catalogue. Call ``propose_casting`` first.
    """
    context = current_context()
    context.note_call("judge_casting")
    graph = _graph(context)
    if graph is None:
        return _no_script()
    if not context.casting:
        return {
            "status": "missing",
            "reason": "no casting to judge; call propose_casting first",
        }

    catalog = _voice_catalog()
    by_id = {voice.id: voice for voice in catalog}
    casting = {
        character: by_id[voice_id]
        for character, voice_id in context.casting.items()
        if voice_id in by_id
    }
    if not casting:
        return {
            "status": "error",
            "reason": "the current casting names no voice in the catalogue",
        }

    result = await judge_voice_fit(graph, casting, available_voices=catalog, llm=context.llm)
    return {
        "status": "ok",
        "overall_score": result.overall_score,
        "rationale": result.rationale,
        "uncast_characters": result.uncast_characters,
        "characters": [
            {
                "character": fit.character,
                "voice_id": fit.voice_id,
                "score": fit.score,
                "rationale": fit.rationale,
                "findings": [f.message for f in fit.findings],
                "better_options": [
                    {"voice_id": s.voice_id, "score": s.score} for s in fit.suggestions[:2]
                ],
            }
            for fit in result.characters
        ],
    }


def recast_character(character: str, voice_id: str) -> dict:
    """Change one character's voice in the run's working casting, then re-judge.

    Use this to act on the casting judge's suggestions instead of re-proposing
    the whole cast.
    """
    context = current_context()
    context.note_call("recast_character")
    if voice_id not in {v.id for v in _voice_catalog()}:
        return {"status": "error", "reason": f"{voice_id} is not a voice in the catalogue"}
    if character not in context.casting:
        return {
            "status": "error",
            "reason": f"{character} is not in the current casting",
            "cast_characters": sorted(context.casting),
        }
    previous = context.casting[character]
    context.casting[character] = voice_id
    return {
        "status": "ok",
        "character": character,
        "previous_voice_id": previous,
        "voice_id": voice_id,
    }


# --------------------------------------------------------------------------- #
# Previz judge agent
# --------------------------------------------------------------------------- #
def judge_previz() -> dict:
    """Score the saved shot lists on coverage, continuity, variety and pacing.

    Runs the animatic judge over every scene that has a shot list, reusing the
    continuity validator's rules for the chosen grammar profile.
    """
    context = current_context()
    context.note_call("judge_previz")
    graph = _graph(context)
    if graph is None:
        return _no_script()
    shotlists = _shotlists(context, graph)
    if not shotlists:
        return {
            "status": "missing",
            "reason": "no shot lists to judge; generate a shot list first",
        }

    result = judge_animatic(graph, shotlists, grammar_profile=context.grammar_profile)
    return {
        "status": "ok",
        "overall_score": result.overall_score,
        "coverage_score": result.coverage_score,
        "continuity_score": result.continuity_score,
        "variety_score": result.variety_score,
        "pacing_score": result.pacing_score,
        "rationale": result.rationale,
        "scenes": [
            {"scene_ordinal": s.scene_ordinal, "score": s.score, "shot_count": s.shot_count}
            for s in result.scenes
        ],
        "findings": [
            {
                "code": f.code,
                "severity": f.severity,
                "message": f.message,
                "scene_ordinal": f.scene_ordinal,
                "shot_ordinal": f.shot_ordinal,
            }
            for f in result.findings[:10]
        ],
    }


# --------------------------------------------------------------------------- #
# Render-planning agent
# --------------------------------------------------------------------------- #
def plan_scene_render(scene_ordinal: int) -> dict:
    """Turn a scene's shot list into render prompts and a cost estimate.

    Assembles the deterministic Veo/board prompt for every shot and prices the
    scene through the real Veo and Gemini-TTS estimators, so the cost is known
    before a single credit is spent. Plans only — it renders nothing.
    """
    context = current_context()
    context.note_call("plan_scene_render")
    graph = _graph(context)
    if graph is None:
        return _no_script()
    scene = next((s for s in graph.scenes if s.ordinal == scene_ordinal), None)
    if scene is None:
        return {"status": "error", "reason": f"no scene with ordinal {scene_ordinal}"}
    record = context.repo.get_shotlist(context.project_id, scene_ordinal)
    if record is None:
        return {
            "status": "missing",
            "scene_ordinal": scene_ordinal,
            "reason": "no shot list saved for this scene yet",
        }

    from app.adapters.google_tts import GoogleTTSAdapter
    from app.adapters.veo import VeoAdapter

    prompts = [
        {
            "shot_ordinal": shot.ordinal,
            "duration_s": _SHOT_SECONDS,
            "prompt": board_prompt(
                shot,
                {},
                {},
                scene.location,
                scene.time_of_day,
                None,
                context.grammar_profile,
            ),
        }
        for shot in record.shotlist.shots
    ]

    # Constructing an adapter resolves no credentials and opens no socket, and
    # estimate_cost_cents is pure arithmetic over the published list price.
    video_cents = sum(VeoAdapter().estimate_cost_cents(_SHOT_SECONDS) for _ in prompts)
    speech = " ".join(line.text for line in scene.lines if line.kind in ("dialogue", "narration"))
    audio_cents = GoogleTTSAdapter().estimate_cost_cents(speech) if speech else 0

    return {
        "status": "ok",
        "scene_ordinal": scene_ordinal,
        "shot_count": len(prompts),
        "estimated_video_cost_cents": video_cents,
        "estimated_audio_cost_cents": audio_cents,
        "estimated_total_cost_cents": video_cents + audio_cents,
        "cost_cap_cents": getattr(
            context.repo.get_project(context.project_id), "cost_cap_cents", None
        ),
        "shot_prompts": prompts,
    }
