"""The edit assistant: prose plus a *proposal*, in the ops the API already takes.

A chat that edits the story graph, not the render. The user says "Mara sounds
too cheerful in the doorway beat" and gets back two things: an answer in
English, and a concrete batch of ops from the closed vocabulary in
:mod:`app.render.timeline_edits`. Nothing lands until the user clicks Apply,
and Apply is the existing ``POST .../timeline/edits`` endpoint — this module
never mutates anything.

Why it lives here, next to the vocabulary rather than under ``app.agents``: the
only thing this module can say is a ``TimelineEdit``, and it is pure in the same
way its neighbour is — no repository, no HTTP, no provider SDK (the
``LLMProvider`` protocol is a type, not an import of ``google.*``). ``app.agents``
is the ADK network tier, where specialists own tool belts and delegate; a
single-turn proposer parked there would claim membership in a network it is not
part of.

**Every op is validated before it is ever shown.** A model that hallucinates
``line_ordinal: 40`` in a four-line scene, or an emotion outside the twelve, or a
shot edit in a scene with no shot list, must not produce a card with an Apply
button that 422s. So each candidate op is (1) parsed into the real
``TimelineEdit`` model and (2) dry-run through the real ``apply_edits`` against
the real current state, in order, each op seeing the state its predecessors
leave. What survives is a batch that will apply; what does not survives only as
a line in ``dropped``, which the panel can show to say the assistant overreached.

Ops that would change nothing are dropped too. Apply on a no-op looks like a
broken button, and an assistant that pads its proposal to seem busy is the
dishonesty this design exists to avoid.

**Undo is exact or it is absent.** Each surviving op's inverse is computed from
the state it is about to change — the prior speaker, the prior emotion, the
prior coverage — so undo replays real values rather than guessing.
``insert_shot`` has no inverse in the vocabulary (there is no remove-shot op), so
a batch containing one is reported as not undoable rather than half-undone. One
thing undo deliberately does not restore is attribution *provenance*: putting a
speaker back still leaves the line marked ``manual`` at confidence 1.0, because
a human really did touch it, and rewriting that back to ``cue`` would falsify
how the graph came to say what it says.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app.adapters.base import LLMProvider
from app.ingest.elements import AttributedLine, NormalizedScene, StoryGraph
from app.nlp.emotion import EMOTIONS
from app.render.audio.model import SceneRenderSettings
from app.render.timeline_edits import (
    EditResult,
    InsertShot,
    ReassignLineCharacter,
    SetAmbienceDuck,
    SetLineEmotion,
    SetScenePacing,
    SetShotCoverage,
    TimelineEdit,
    TimelineEditError,
    apply_edits,
)
from app.shotlist.schema import SceneShotList, ShotSpec

# A proposal is something a person reads before clicking Apply, so it is capped
# at what a person will actually read. The model is told the same number.
MAX_PROPOSED_EDITS = 12

# Sampling: this is a structured-output task with one right shape, so the
# temperature is low. Gemini's JSON mime type is passed too — the parser below
# stays tolerant anyway, because "responds in JSON" is a request, not a promise.
_LLM_PARAMS: dict[str, Any] = {
    "temperature": 0.2,
    "maxOutputTokens": 2048,
    "responseMimeType": "application/json",
}

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?|\n?```\s*$", re.MULTILINE)

_EDIT_ADAPTER: TypeAdapter[TimelineEdit] = TypeAdapter(TimelineEdit)


# ---------------------------------------------------------------------------
# Wire models
# ---------------------------------------------------------------------------


class AssistTurn(BaseModel):
    """One turn of the conversation so far, as the panel has it."""

    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ProposedEdit(BaseModel):
    """One validated op and the sentence that describes it.

    The wording is written here rather than in the panel because the vocabulary
    is defined here: a TypeScript re-implementation of "what does
    ``set_shot_coverage`` mean" is a second definition free to drift from the
    first.
    """

    edit: TimelineEdit
    summary: str


class EditBatch(BaseModel):
    """A screened batch: what will apply, what undoes it, what was refused."""

    edits: list[ProposedEdit] = Field(default_factory=list)
    # The inverse batch, already in the order it must be sent (last op undone
    # first). Empty whenever ``undo_blocked_by`` is set.
    undo: list[TimelineEdit] = Field(default_factory=list)
    undo_summary: list[str] = Field(default_factory=list)
    undo_blocked_by: str | None = None
    # Ops the model produced that were never shown, and why. Surfaced rather
    # than swallowed: a proposal that quietly shrank is a proposal that lied.
    dropped: list[str] = Field(default_factory=list)


class AssistProposal(EditBatch):
    """The assistant's whole answer: prose, plus the batch it proposes."""

    reply: str
    provider: str = ""
    model: str = ""


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = f"""\
You are the edit assistant for one scene of a screenplay held as a story graph. \
You do not render audio or video and you do not apply anything: you answer the \
user, and you PROPOSE edits that the user reviews and applies themselves.

Respond with ONLY JSON - no prose outside it, no markdown fences:
{{
  "reply": "<your answer to the user, 1-3 sentences, plain English>",
  "edits": [ <zero or more ops, in the order they should be applied> ]
}}

The ops are a closed vocabulary. These six edits are the only ones that exist:

1. {{"op": "reassign_line_character", "line_ordinal": <int>, "character_name": \
"<NAME>" or null, "allow_new": <bool, default false>}}
   Change who speaks a line. Only DIALOGUE lines have a speaker. null hands the \
line to the narrator. Set allow_new true only when deliberately introducing a \
name the story graph has never seen.

2. {{"op": "set_line_emotion", "line_ordinal": <int>, "emotion": "<label>" or null}}
   How a line is delivered; null is neutral. The only labels are: \
{", ".join(sorted(EMOTIONS))}.

3. {{"op": "set_scene_pacing", "pacing": <float 0.25-4.0>}}
   Scale the silence between clips across the whole scene. Below 1 tightens, \
above 1 lets it breathe. Clip durations never change - this is dead air only.

4. {{"op": "set_ambience_duck", "depth": <float 0.0-1.0>}}
   How hard the ambience bed ducks under speech. 0 is no ducking, 0.5 is the \
engine default, 1 is hardest.

5. {{"op": "insert_shot", "after_ordinal": <int> or null, "shot": {{"size": \
"<ecu|cu|mcu|ms|mws|ws|ews|insert|pov>", "subjects": ["<NAME>"], "covers_lines": \
[<int>, ...], "intent": "<why this shot exists, max 200 chars>"}}}}
   Add a shot. A null after_ordinal inserts at the head. covers_lines must name \
at least one line that exists in this scene. The camera fields (axis_side, \
lens_mm, camera_height, movement, eyeline) are optional and default to a neutral \
setup - send one only when the user asked for it.

6. {{"op": "set_shot_coverage", "shot_ordinal": <int>, "covers_lines": [<int>, ...]}}
   Repoint which lines a shot covers. An empty list deliberately uncovers it.

Rules:
- Use only line ordinals, shot ordinals and character names that appear in the \
scene given below. Never guess at one.
- At most {MAX_PROPOSED_EDITS} ops. Propose the smallest batch that does what was \
asked; do not pad it.
- Never propose an op that would change nothing (an emotion a line already has, \
a pacing already set).
- If what the user wants cannot be said in these six ops - a line rewritten, a \
scene cut, the audio re-rendered - say so in "reply" and return "edits": []. \
Never invent an op name or a field.
- Write "reply" as someone proposing, not reporting: nothing has been applied \
yet. An applied edit marks any existing render stale, and the audio does not \
change until the scene is re-rendered.
"""


def _format_line(line: AttributedLine) -> str:
    return (
        f"{line.ordinal} | {line.kind} | {line.character_name or '-'} "
        f"| {line.emotion or '-'} | {line.text}"
    )


def _format_shot(shot: ShotSpec) -> str:
    covers = ", ".join(str(n) for n in shot.covers_lines) or "nothing"
    subjects = ", ".join(shot.subjects) or "-"
    return f"{shot.ordinal} | {shot.size} | {subjects} | covers {covers} | {shot.intent}"


def build_assist_prompt(
    graph: StoryGraph,
    scene: NormalizedScene,
    shotlist: SceneShotList | None,
    settings: SceneRenderSettings,
    message: str,
    history: Sequence[AssistTurn] = (),
) -> tuple[str, str]:
    """Build the (system, user) pair for one assist turn.

    The scene goes in whole. A model cannot propose ``line_ordinal: 7``
    responsibly without seeing line 7, and the alternative — a retrieval step
    over a scene that is a few dozen lines long — buys nothing but a new way to
    be wrong.
    """
    header = f"Scene {scene.ordinal}: {scene.slugline or '(no slugline)'}"
    if scene.time_of_day:
        header += f" (time of day: {scene.time_of_day})"

    known = ", ".join(sorted(c.canonical_name for c in graph.characters)) or "(none)"

    if shotlist is None:
        shots = (
            "No shot list has been authored for this scene, so insert_shot and "
            "set_shot_coverage cannot be used here."
        )
    else:
        shots = "Shots (ordinal | size | subjects | coverage | intent):\n" + "\n".join(
            _format_shot(shot) for shot in shotlist.shots
        )

    conversation = (
        "\n".join(f"{turn.role}: {turn.content}" for turn in history)
        if history
        else "(this is the first message)"
    )

    user = (
        f"{header}\n\n"
        "Lines (ordinal | kind | speaker | emotion | text):\n"
        + "\n".join(_format_line(line) for line in scene.lines)
        + f"\n\nCharacters in the story graph: {known}\n"
        f"Render settings: pacing {settings.pacing:g}, "
        f"ambience duck {settings.ambience_duck:g}\n\n"
        f"{shots}\n\n"
        f"Conversation so far:\n{conversation}\n\n"
        f"The user says:\n{message}"
    )
    return _SYSTEM_PROMPT, user


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def parse_assist_reply(raw: str) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Split raw model output into (reply, candidate ops, complaints).

    Never raises. Output that is not the envelope is still shown to the user as
    the assistant's answer — the model said something, and hiding it behind
    "malformed response" helps nobody — but it proposes nothing.
    """
    text = _FENCE_RE.sub("", raw).strip()
    start, end = text.find("{"), text.rfind("}")
    payload: object = None
    if start != -1 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
        except ValueError:
            payload = None

    if not isinstance(payload, dict):
        return (
            text,
            [],
            ["the model did not answer with the JSON envelope, so no edits were read"],
        )

    complaints: list[str] = []
    reply = payload.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        reply = text
        complaints.append("the model's answer carried no 'reply' field")

    raw_edits = payload.get("edits")
    if raw_edits is None:
        return reply, [], complaints
    if not isinstance(raw_edits, list):
        complaints.append("'edits' was not a list, so no edits were read")
        return reply, [], complaints

    candidates = [op for op in raw_edits if isinstance(op, dict)]
    if len(candidates) != len(raw_edits):
        complaints.append("some entries in 'edits' were not objects")
    if len(candidates) > MAX_PROPOSED_EDITS:
        complaints.append(
            f"the model proposed {len(candidates)} ops; only the first "
            f"{MAX_PROPOSED_EDITS} were considered"
        )
        candidates = candidates[:MAX_PROPOSED_EDITS]
    return reply, candidates, complaints


# ---------------------------------------------------------------------------
# Human wording, and inverses
# ---------------------------------------------------------------------------


def _line(scene: NormalizedScene, ordinal: int) -> AttributedLine | None:
    return next((l for l in scene.lines if l.ordinal == ordinal), None)


def _shot(shotlist: SceneShotList | None, ordinal: int) -> ShotSpec | None:
    if shotlist is None:
        return None
    return next((s for s in shotlist.shots if s.ordinal == ordinal), None)


def _lines_phrase(ordinals: Sequence[int]) -> str:
    if not ordinals:
        return "no lines"
    if len(ordinals) == 1:
        return f"line {ordinals[0]}"
    return "lines " + ", ".join(str(n) for n in ordinals)


def describe(
    edit: TimelineEdit,
    scene: NormalizedScene,
    shotlist: SceneShotList | None,
    settings: SceneRenderSettings,
) -> str:
    """One sentence for one op, read against the state it is about to change."""
    match edit:
        case ReassignLineCharacter():
            line = _line(scene, edit.line_ordinal)
            previous = (line.character_name if line else None) or "the narrator"
            target = (
                edit.character_name.strip().upper()
                if edit.character_name is not None
                else "the narrator"
            )
            return f"Reassign line {edit.line_ordinal} from {previous} to {target}"
        case SetLineEmotion():
            line = _line(scene, edit.line_ordinal)
            was = (line.emotion if line else None) or "neutral"
            target = (edit.emotion or "neutral").strip().lower()
            return f"Set line {edit.line_ordinal} delivery to {target} (was {was})"
        case SetScenePacing():
            verb = (
                "Tighten"
                if edit.pacing < settings.pacing
                else "Loosen"
                if edit.pacing > settings.pacing
                else "Set"
            )
            return (
                f"{verb} scene pacing to {edit.pacing:g}x (was {settings.pacing:g}x) "
                "— the gaps, never the clips"
            )
        case SetAmbienceDuck():
            return (
                f"Duck ambience {edit.depth * 100:.0f}% under speech "
                f"(was {settings.ambience_duck * 100:.0f}%)"
            )
        case InsertShot():
            where = (
                f"after shot {edit.after_ordinal}"
                if edit.after_ordinal is not None
                else "at the head of the scene"
            )
            subjects = ", ".join(edit.shot.subjects) or "no named subject"
            return (
                f"Insert a {edit.shot.size.upper()} of {subjects} {where}, covering "
                f"{_lines_phrase(edit.shot.covers_lines)}"
            )
        case SetShotCoverage():
            shot = _shot(shotlist, edit.shot_ordinal)
            was = _lines_phrase(shot.covers_lines) if shot else "unknown lines"
            if not edit.covers_lines:
                return f"Uncover shot {edit.shot_ordinal} (was {was})"
            return (
                f"Repoint shot {edit.shot_ordinal} to cover "
                f"{_lines_phrase(edit.covers_lines)} (was {was})"
            )
    # Unreachable: TimelineEdit is a closed union and every member is handled.
    raise AssertionError(f"no wording for op {edit!r}")


def invert(
    edit: TimelineEdit,
    scene: NormalizedScene,
    shotlist: SceneShotList | None,
    settings: SceneRenderSettings,
) -> TimelineEdit | None:
    """The op that puts this one back, or ``None`` when there isn't one.

    Read from the pre-edit state, so undo restores the value that was actually
    there. ``allow_new`` is set on a reassignment's inverse because the forward
    edit may have taken a character's last line, dropping them from the graph —
    undo has to be allowed to reintroduce the name the edit itself removed.
    """
    match edit:
        case ReassignLineCharacter():
            line = _line(scene, edit.line_ordinal)
            if line is None:
                return None
            return ReassignLineCharacter(
                op="reassign_line_character",
                line_ordinal=edit.line_ordinal,
                character_name=line.character_name,
                allow_new=True,
            )
        case SetLineEmotion():
            line = _line(scene, edit.line_ordinal)
            if line is None:
                return None
            return SetLineEmotion(
                op="set_line_emotion",
                line_ordinal=edit.line_ordinal,
                emotion=line.emotion,
            )
        case SetScenePacing():
            return SetScenePacing(op="set_scene_pacing", pacing=settings.pacing)
        case SetAmbienceDuck():
            return SetAmbienceDuck(op="set_ambience_duck", depth=settings.ambience_duck)
        case InsertShot():
            # The vocabulary has no remove-shot op, so an insert is a one-way door.
            return None
        case SetShotCoverage():
            shot = _shot(shotlist, edit.shot_ordinal)
            if shot is None:
                return None
            return SetShotCoverage(
                op="set_shot_coverage",
                shot_ordinal=edit.shot_ordinal,
                covers_lines=list(shot.covers_lines),
            )
    raise AssertionError(f"no inverse rule for op {edit!r}")


# ---------------------------------------------------------------------------
# Screening
# ---------------------------------------------------------------------------


def _scene_of(result: EditResult, scene_ordinal: int) -> NormalizedScene:
    return next(s for s in result.graph.scenes if s.ordinal == scene_ordinal)


def _op_label(candidate: dict[str, Any]) -> str:
    op = candidate.get("op")
    return op if isinstance(op, str) and op else "(unnamed op)"


def _validation_detail(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else str(error["msg"])


def screen_edits(
    graph: StoryGraph,
    scene_ordinal: int,
    shotlist: SceneShotList | None,
    settings: SceneRenderSettings,
    candidates: Sequence[dict[str, Any]],
) -> EditBatch:
    """Turn raw candidate ops into a batch that is guaranteed to apply.

    Each op is parsed into the real model and then dry-run through the real
    ``apply_edits`` against the state its predecessors leave — which is exactly
    the state ``POST .../timeline/edits`` will see. An op that raises, or that
    changes nothing, is dropped with the engine's own reason. The survivors are
    then re-run as one batch, so what the user is offered is the all-or-nothing
    batch the endpoint will accept rather than a hopeful list.
    """
    scene = next((s for s in graph.scenes if s.ordinal == scene_ordinal), None)
    if scene is None:
        return EditBatch(dropped=[f"scene {scene_ordinal} is not in this story graph"])

    kept: list[ProposedEdit] = []
    inverses: list[TimelineEdit] = []
    inverse_summaries: list[str] = []
    dropped: list[str] = []
    blocked: str | None = None

    # The running state: what each op is validated against, and what its wording
    # and its inverse are read from.
    state_graph, state_scene = graph, scene
    state_shotlist, state_settings = shotlist, settings

    for candidate in candidates:
        try:
            edit = _EDIT_ADAPTER.validate_python(candidate)
        except ValidationError as exc:
            dropped.append(f"{_op_label(candidate)}: {_validation_detail(exc)}")
            continue

        try:
            result = apply_edits(
                state_graph, scene_ordinal, state_shotlist, state_settings, [edit]
            )
        except TimelineEditError as exc:
            dropped.append(f"{edit.op}: {exc}")
            continue

        if not (
            result.graph_changed or result.shotlist_changed or result.settings_changed
        ):
            dropped.append(f"{edit.op}: would change nothing")
            continue

        summary = describe(edit, state_scene, state_shotlist, state_settings)
        inverse = invert(edit, state_scene, state_shotlist, state_settings)
        if inverse is None:
            blocked = blocked or (
                f"{summary} cannot be undone — the edit vocabulary has no op that "
                "removes a shot"
            )
        else:
            inverses.append(inverse)
            inverse_summaries.append(
                describe(
                    inverse,
                    _scene_of(result, scene_ordinal),
                    result.shotlist,
                    result.settings,
                )
            )

        kept.append(ProposedEdit(edit=edit, summary=summary))
        state_graph = result.graph
        state_scene = _scene_of(result, scene_ordinal)
        state_shotlist = result.shotlist
        state_settings = result.settings

    if kept:
        # Belt and braces on the all-or-nothing contract: each survivor was
        # checked against the running state, so replaying them together from the
        # original state must also succeed. If it somehow does not, offer nothing
        # rather than a card whose Apply button 422s.
        try:
            apply_edits(graph, scene_ordinal, shotlist, settings, [p.edit for p in kept])
        except TimelineEditError as exc:
            return EditBatch(
                dropped=[*dropped, f"the batch did not survive a final dry run: {exc}"]
            )

    if blocked is not None:
        inverses, inverse_summaries = [], []

    return EditBatch(
        edits=kept,
        # Undo replays the inverses last-first: the state each one restores is
        # the state the op after it was measured against.
        undo=list(reversed(inverses)),
        undo_summary=list(reversed(inverse_summaries)),
        undo_blocked_by=blocked,
        dropped=dropped,
    )


# ---------------------------------------------------------------------------
# One turn
# ---------------------------------------------------------------------------


async def propose_edits(
    prompt: tuple[str, str],
    graph: StoryGraph,
    scene_ordinal: int,
    shotlist: SceneShotList | None,
    settings: SceneRenderSettings,
    llm: LLMProvider,
) -> AssistProposal:
    """One turn: ask the model, screen everything it proposed, answer.

    The prompt arrives already built (``build_assist_prompt``) rather than being
    assembled here, so the caller can price the *exact* strings it is about to
    send: the cost governor's estimate and the paid call must never be able to
    drift apart.

    The only paid call in this module, and the caller has already put it past
    that governor. Nothing here retries: a proposal is cheap to ask for again by
    hand, and a silent second call would spend a second time.
    """
    system, user = prompt
    result = await llm.complete(system, user, dict(_LLM_PARAMS))
    reply, candidates, complaints = parse_assist_reply(result.text)
    batch = screen_edits(graph, scene_ordinal, shotlist, settings, candidates)

    return AssistProposal(
        reply=reply,
        edits=batch.edits,
        undo=batch.undo,
        undo_summary=batch.undo_summary,
        undo_blocked_by=batch.undo_blocked_by,
        dropped=[*complaints, *batch.dropped],
        provider=result.provider,
        model=result.model,
    )
