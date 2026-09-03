"""Timeline edit operations — the editor tier over the story-graph IR.

The timeline is an editor, not a viewer. Every edit the UI offers lands here as
an op that mutates the **IR** (who speaks a line, how it is delivered), the
scene's shot list, or the scene's render settings — never the rendered WAV.
Voices, emotion, gaps and ducking are all re-derived on the next render, which
is what keeps the IR the product instead of a caption track over an audio file.

Pure module: no repository, no HTTP, no provider. Callers hand in the current
state and get new state back plus the flags saying what to persist and whether
an existing render still matches what the IR now says.

Ops are all-or-nothing: work happens on copies and the first invalid op aborts
the batch, so a bad ordinal halfway through a batch cannot leave the scene half
edited.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Annotated, Literal

from pydantic import BaseModel, Field, ValidationError

from app.ingest.elements import (
    AttributedLine,
    NormalizedCharacter,
    NormalizedScene,
    StoryGraph,
)
from app.nlp.emotion import EMOTIONS
from app.render.audio.model import SceneRenderSettings
from app.shotlist.schema import (
    AxisSide,
    CameraHeight,
    Eyeline,
    Movement,
    SceneShotList,
    ShotSize,
    ShotSpec,
)

# Mirrors app.render.audio.pipeline's spoken-line filter (kept local so the edit
# layer does not drag in the numpy DSP stack). Only these kinds ever reach TTS,
# so only these can carry an emotion that changes the audio.
_SPOKEN_KINDS = frozenset({"dialogue", "action", "narration"})


class TimelineEditError(ValueError):
    """An op that cannot be applied to this scene.

    Terminal by construction — unknown line, unknown character, bad shot
    ordinal. The caller maps it to 4xx; nothing here is worth retrying.
    """

    def __init__(self, message: str, *, index: int | None = None) -> None:
        self.index = index
        super().__init__(message)


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


class ReassignLineCharacter(BaseModel):
    """Change who speaks a line — the highest-value edit in the product.

    Attribution accuracy is what the IR is sold on, so a correction is treated
    as ground truth exactly like the manual line patch: source ``manual``,
    confidence 1.0. ``character_name: null`` un-attributes the line (it falls
    back to the narrator voice), which is the honest state for a quote whose
    speaker is genuinely unresolved.
    """

    op: Literal["reassign_line_character"]
    line_ordinal: int = Field(ge=1)
    character_name: str | None
    # Reassigning to a name the graph has never seen is nearly always a typo, so
    # it is rejected unless the caller says it is introducing someone.
    allow_new: bool = False


class SetLineEmotion(BaseModel):
    """Set or clear a line's delivery emotion (drives emotional TTS)."""

    op: Literal["set_line_emotion"]
    line_ordinal: int = Field(ge=1)
    emotion: str | None


class SetScenePacing(BaseModel):
    """Scale the dead air between clips. <1 tightens ("tighten pacing")."""

    op: Literal["set_scene_pacing"]
    pacing: float = Field(ge=0.25, le=4.0)


class SetAmbienceDuck(BaseModel):
    """How hard ambience ducks under speech; 0 = no duck, 0.5 = engine default."""

    op: Literal["set_ambience_duck"]
    depth: float = Field(ge=0.0, le=1.0)


class NewShot(BaseModel):
    """A shot to insert: the ``ShotSpec`` contract minus ``ordinal``, which the
    insert assigns because ``SceneShotList`` requires contiguous 1..N.

    The mechanical camera fields default to a neutral, non-committal setup so an
    assisted edit ("add a reaction shot on line 5") is a three-field call; the
    continuity validator re-runs afterwards and warns if that default is wrong
    for the scene, which is the correct division of labour (continuity warns,
    never blocks).
    """

    size: ShotSize
    subjects: list[str]
    # A shot covering no line cannot be placed on the timeline's visual lane,
    # so an inserted one must say what it is on.
    covers_lines: list[int] = Field(min_length=1)
    intent: str = Field(max_length=200)
    axis_side: AxisSide = "neutral"
    lens_mm: int = Field(default=50, ge=8, le=300)
    camera_height: CameraHeight = "eye"
    movement: Movement = "static"
    eyeline: Eyeline = "none"

    def to_spec(self, ordinal: int) -> ShotSpec:
        return ShotSpec(ordinal=ordinal, **self.model_dump())


class InsertShot(BaseModel):
    """Insert a shot after ``after_ordinal`` (null inserts at the head)."""

    op: Literal["insert_shot"]
    after_ordinal: int | None = Field(default=None, ge=1)
    shot: NewShot


class SetShotCoverage(BaseModel):
    """Repoint which lines a shot covers. Empty deliberately uncovers it."""

    op: Literal["set_shot_coverage"]
    shot_ordinal: int = Field(ge=1)
    covers_lines: list[int]


TimelineEdit = Annotated[
    ReassignLineCharacter
    | SetLineEmotion
    | SetScenePacing
    | SetAmbienceDuck
    | InsertShot
    | SetShotCoverage,
    Field(discriminator="op"),
]


class EditResult(BaseModel):
    """New state plus what changed.

    ``stale_reasons`` is non-empty exactly when an already-rendered WAV no
    longer matches the IR — the flags let the caller persist only what moved and
    mark the render stale instead of serving audio the timeline disagrees with.
    Shot edits never appear here: the visual lane is projected from the shot list
    at read time, so it is never stale.
    """

    graph: StoryGraph
    shotlist: SceneShotList | None
    settings: SceneRenderSettings
    graph_changed: bool = False
    shotlist_changed: bool = False
    settings_changed: bool = False
    stale_reasons: list[str] = Field(default_factory=list)

    @property
    def invalidates_audio(self) -> bool:
        return bool(self.stale_reasons)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


def _find_line(scene: NormalizedScene, line_ordinal: int) -> AttributedLine:
    line = next((l for l in scene.lines if l.ordinal == line_ordinal), None)
    if line is None:
        raise TimelineEditError(
            f"line {line_ordinal} not found in scene {scene.ordinal}"
        )
    return line


def _referenced_names(graph: StoryGraph) -> set[str]:
    """Every character name any line still points at (dialogue or parenthetical)."""
    return {
        line.character_name
        for scene in graph.scenes
        for line in scene.lines
        if line.character_name
    }


def _resync_characters(graph: StoryGraph, freed_name: str | None) -> None:
    """Recompute the character list from the lines after an attribution change.

    ``line_count`` is derived data (dialogue lines per speaker, graph-wide), so
    it is recomputed rather than incremented — that stays correct however many
    reassignments a batch makes. A speaker an edit stripped of its last
    reference is dropped, because a ghost character would show up in casting and
    in the judges; characters that never had lines (cued but silent) are left
    alone since no edit created them.
    """
    counts = Counter(
        line.character_name
        for scene in graph.scenes
        for line in scene.lines
        if line.kind == "dialogue" and line.character_name
    )
    known = {character.canonical_name for character in graph.characters}
    for name in counts:
        if name not in known:
            graph.characters.append(NormalizedCharacter(canonical_name=name))
            known.add(name)
    for character in graph.characters:
        character.line_count = counts.get(character.canonical_name, 0)

    if freed_name is not None and freed_name not in _referenced_names(graph):
        graph.characters = [
            c for c in graph.characters if c.canonical_name != freed_name
        ]


def _apply_reassign(
    graph: StoryGraph, scene: NormalizedScene, edit: ReassignLineCharacter
) -> str | None:
    line = _find_line(scene, edit.line_ordinal)
    if line.kind != "dialogue":
        raise TimelineEditError(
            f"line {edit.line_ordinal} is a {line.kind} line; only dialogue has a speaker"
        )

    name: str | None = None
    if edit.character_name is not None:
        # Canonical names are upper-cased at ingest (normalize._canonical_name);
        # matching that here keeps 'Tom' and 'TOM' one character, not two.
        name = edit.character_name.strip().upper()
        if not name:
            raise TimelineEditError("character_name must not be blank")
        known = {c.canonical_name for c in graph.characters}
        if name not in known and not edit.allow_new:
            raise TimelineEditError(
                f"unknown character {name!r}; pass allow_new to introduce a new one"
            )

    previous = line.character_name
    if previous == name:
        return None

    line.character_name = name
    # Human corrections are ground truth (mirrors PATCH .../lines/{ordinal}).
    line.attribution_source = "manual"
    line.attribution_confidence = 1.0
    _resync_characters(graph, previous)
    return (
        f"line {edit.line_ordinal} reassigned from "
        f"{previous or 'narrator'} to {name or 'narrator'}"
    )


def _apply_emotion(scene: NormalizedScene, edit: SetLineEmotion) -> str | None:
    line = _find_line(scene, edit.line_ordinal)
    if line.kind not in _SPOKEN_KINDS:
        raise TimelineEditError(
            f"line {edit.line_ordinal} is a {line.kind} line; it is never spoken, "
            "so an emotion would not reach TTS"
        )
    emotion: str | None = None
    if edit.emotion is not None:
        emotion = edit.emotion.strip().lower()
        if emotion not in EMOTIONS:
            raise TimelineEditError(
                f"unknown emotion {edit.emotion!r}; expected one of "
                f"{', '.join(sorted(EMOTIONS))}"
            )
    if line.emotion == emotion:
        return None
    line.emotion = emotion
    return f"line {edit.line_ordinal} emotion set to {emotion or 'neutral'}"


def _rebuild_shotlist(shotlist: SceneShotList, shots: list[ShotSpec]) -> SceneShotList:
    """Renumber to contiguous 1..N and re-validate through the schema.

    Renumbering is forced on us by ``SceneShotList``'s contiguity rule, which
    means an insert shifts the ordinals of every shot after it. The caller
    re-derives continuity findings for that reason; per-shot video renders are
    still keyed by the old ordinal and would need re-keying before shot video
    and shot list can be trusted together.
    """
    renumbered = [
        shot.model_copy(update={"ordinal": index}) for index, shot in enumerate(shots, 1)
    ]
    try:
        return SceneShotList(
            scene_ordinal=shotlist.scene_ordinal,
            action_axis=shotlist.action_axis,
            shots=renumbered,
        )
    except ValidationError as exc:
        detail = exc.errors()[0]["msg"]
        raise TimelineEditError(f"resulting shot list is invalid: {detail}") from exc


def _require_shotlist(
    shotlist: SceneShotList | None, scene: NormalizedScene
) -> SceneShotList:
    if shotlist is None:
        raise TimelineEditError(
            f"no shot list for scene {scene.ordinal}; POST one before editing shots"
        )
    return shotlist


def _check_covers(scene: NormalizedScene, covers_lines: list[int]) -> list[int]:
    """Validate coverage against the IR and drop duplicates, keeping order."""
    ordinals = {line.ordinal for line in scene.lines}
    missing = [n for n in covers_lines if n not in ordinals]
    if missing:
        raise TimelineEditError(
            f"covers_lines references line(s) {missing} absent from scene {scene.ordinal}"
        )
    seen: set[int] = set()
    return [n for n in covers_lines if not (n in seen or seen.add(n))]


def _apply_insert_shot(
    shotlist: SceneShotList | None, scene: NormalizedScene, edit: InsertShot
) -> SceneShotList:
    current = _require_shotlist(shotlist, scene)
    covers = _check_covers(scene, edit.shot.covers_lines)
    shots = list(current.shots)
    if edit.after_ordinal is None:
        position = 0
    else:
        position = next(
            (i + 1 for i, s in enumerate(shots) if s.ordinal == edit.after_ordinal),
            -1,
        )
        if position < 0:
            raise TimelineEditError(
                f"shot {edit.after_ordinal} not found in scene {scene.ordinal}"
            )
    # Ordinal 1 is a placeholder; _rebuild_shotlist renumbers the whole list.
    shots.insert(position, edit.shot.model_copy(update={"covers_lines": covers}).to_spec(1))
    return _rebuild_shotlist(current, shots)


def _apply_set_coverage(
    shotlist: SceneShotList | None, scene: NormalizedScene, edit: SetShotCoverage
) -> SceneShotList | None:
    current = _require_shotlist(shotlist, scene)
    covers = _check_covers(scene, edit.covers_lines)
    shot = next((s for s in current.shots if s.ordinal == edit.shot_ordinal), None)
    if shot is None:
        raise TimelineEditError(
            f"shot {edit.shot_ordinal} not found in scene {scene.ordinal}"
        )
    if shot.covers_lines == covers:
        return None
    shots = [
        s.model_copy(update={"covers_lines": covers}) if s.ordinal == edit.shot_ordinal else s
        for s in current.shots
    ]
    return _rebuild_shotlist(current, shots)


def apply_edits(
    graph: StoryGraph,
    scene_ordinal: int,
    shotlist: SceneShotList | None,
    settings: SceneRenderSettings,
    edits: Sequence[TimelineEdit],
) -> EditResult:
    """Apply a batch of ops to one scene and report what moved.

    Works on copies and validates as it goes, so raising leaves the caller's
    state untouched: a batch is applied whole or not at all.
    """
    working = graph.model_copy(deep=True)
    scene = next((s for s in working.scenes if s.ordinal == scene_ordinal), None)
    if scene is None:
        raise TimelineEditError(f"scene {scene_ordinal} not found")
    working_shotlist = shotlist.model_copy(deep=True) if shotlist is not None else None
    working_settings = settings

    graph_changed = False
    shotlist_changed = False
    settings_changed = False
    reasons: list[str] = []

    for index, edit in enumerate(edits):
        try:
            match edit:
                case ReassignLineCharacter():
                    reason = _apply_reassign(working, scene, edit)
                    if reason:
                        graph_changed = True
                        reasons.append(reason)
                case SetLineEmotion():
                    reason = _apply_emotion(scene, edit)
                    if reason:
                        graph_changed = True
                        reasons.append(reason)
                case SetScenePacing():
                    if working_settings.pacing != edit.pacing:
                        reasons.append(
                            f"pacing {working_settings.pacing:g} -> {edit.pacing:g}"
                        )
                        working_settings = SceneRenderSettings(
                            pacing=edit.pacing,
                            ambience_duck=working_settings.ambience_duck,
                        )
                        settings_changed = True
                case SetAmbienceDuck():
                    if working_settings.ambience_duck != edit.depth:
                        reasons.append(
                            f"ambience duck {working_settings.ambience_duck:g} -> {edit.depth:g}"
                        )
                        working_settings = SceneRenderSettings(
                            pacing=working_settings.pacing, ambience_duck=edit.depth
                        )
                        settings_changed = True
                case InsertShot():
                    working_shotlist = _apply_insert_shot(working_shotlist, scene, edit)
                    shotlist_changed = True
                case SetShotCoverage():
                    updated = _apply_set_coverage(working_shotlist, scene, edit)
                    if updated is not None:
                        working_shotlist = updated
                        shotlist_changed = True
        except TimelineEditError as exc:
            raise TimelineEditError(str(exc), index=index) from exc

    return EditResult(
        graph=working,
        shotlist=working_shotlist,
        settings=working_settings,
        graph_changed=graph_changed,
        shotlist_changed=shotlist_changed,
        settings_changed=settings_changed,
        stale_reasons=reasons,
    )
