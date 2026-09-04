"""Scene shot lists, continuity findings, and the scene audio render.

POST /shotlist is the manual authoring path for M1; the LLM shot-list
generator is a separate module and will feed the same endpoint contract.

**Where the WAV lives.** A rendered scene is megabytes of audio and a container
is neither durable nor roomy: with a bucket configured (:mod:`app.storage`) the
render uploads its WAV and the repository keeps a zero-byte placeholder, so a
restart no longer loses the take and a long session no longer accumulates them
in RAM. ``GET /audio`` reads the bytes back on demand. With no bucket the WAV
stays in the repository exactly as before — which is what lets the app run with
no cloud account at all.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response

from app.adapters.base import TTSProvider
from app.api.deps import get_owned_project, get_repo, get_tts
from app.api.repo import AudioRenderRecord, FindingRecord, ProjectRecord, Repository
from app.api.schemas import (
    AudioRenderOut,
    FindingOut,
    FindingPatch,
    SceneMarker,
    SceneTimeline,
    ShotListOut,
    TimelineAmbienceSpan,
    TimelineDialogueClip,
    TimelineEditRequest,
    TimelineSfxMarker,
    TimelineVisualClip,
)
from app.continuity.model import Finding, SceneContext, ShotMeta
from app.continuity.validator import validate_scene
from app.costs import governor
from app.ingest.elements import NormalizedScene, StoryGraph
from app.render.audio.model import SceneRenderSettings, SceneTiming
from app.render.audio.pipeline import render_scene_audio_with_timing
from app.render.timeline_edits import TimelineEditError, apply_edits
from app.shotlist.schema import SceneShotList
from app.storage import ObjectNotFound, ObjectStore, ObjectStoreError, get_object_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects/{project_id}/scenes/{ordinal}", tags=["scenes"])

# Rendered lines mirror app.render.audio.pipeline's own spoken-line filter so
# the pre-flight cost estimate matches what actually gets synthesized.
_SPOKEN_KINDS = {"dialogue", "action", "narration"}

_NARRATOR_VOICE = "en-US-GuyNeural"
_SPEAKER_VOICE_POOL = [
    "en-US-JennyNeural",
    "en-US-AriaNeural",
    "en-GB-RyanNeural",
    "en-GB-SoniaNeural",
    "en-US-ChristopherNeural",
]


def _estimate_cost_cents(scene: NormalizedScene, tts: TTSProvider) -> int:
    return sum(
        tts.estimate_cost_cents(line.text)
        for line in scene.lines
        if line.kind in _SPOKEN_KINDS and line.text.strip()
    )


def _voice_map(scene: NormalizedScene) -> dict[str | None, str]:
    speakers = sorted(
        {
            line.character_name
            for line in scene.lines
            if line.kind == "dialogue" and line.character_name
        }
    )
    voice_map: dict[str | None, str] = {None: _NARRATOR_VOICE}
    for index, speaker in enumerate(speakers):
        voice_map[speaker] = _SPEAKER_VOICE_POOL[index % len(_SPEAKER_VOICE_POOL)]
    return voice_map


def _get_scene_or_404(
    repo: Repository, project_id: str, ordinal: int
) -> tuple[StoryGraph, NormalizedScene]:
    script = repo.get_script(project_id)
    if script is None:
        raise HTTPException(status_code=404, detail="No script uploaded yet")
    scene = next((s for s in script.graph.scenes if s.ordinal == ordinal), None)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene {ordinal} not found")
    return script.graph, scene


def _scene_context(graph: StoryGraph, scene: NormalizedScene) -> SceneContext:
    speakers = [
        line.character_name
        for line in scene.lines
        if line.kind == "dialogue" and line.character_name
    ]
    ordered = sorted(graph.scenes, key=lambda s: s.ordinal)
    index = ordered.index(scene)
    prev_time = ordered[index - 1].time_of_day if index > 0 else None
    return SceneContext(
        ordinal=scene.ordinal,
        dialogue_speakers=speakers,
        character_names={name: name for name in speakers},
        time_of_day=scene.time_of_day,
        prev_scene_time_of_day=prev_time,
    )


def _run_validator(
    project: ProjectRecord, graph: StoryGraph, scene: NormalizedScene, body: SceneShotList
) -> list[Finding]:
    if project.validator_mode == "off":
        return []
    shots = [
        ShotMeta(
            ordinal=s.ordinal,
            size=s.size,
            subject_ids=s.subjects,
            axis_side=s.axis_side,
            lens_mm=s.lens_mm,
            camera_height=s.camera_height,
            movement=s.movement,
            eyeline=s.eyeline,
        )
        for s in body.shots
    ]
    findings = validate_scene(_scene_context(graph, scene), shots, project.grammar_profile)
    if project.validator_mode == "lenient":
        findings = [f for f in findings if f.severity != "info"]
    return findings


def _finding_out(record: FindingRecord) -> FindingOut:
    return FindingOut(
        id=record.id,
        rule_code=record.rule_code,
        severity=record.severity,
        message=record.message,
        shot_ordinal=record.shot_ordinal,
        deliberate=record.deliberate,
        deliberate_note=record.deliberate_note,
    )


@router.post("/shotlist", response_model=list[FindingOut], status_code=201)
def post_shotlist(
    ordinal: int,
    body: SceneShotList,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> list[FindingOut]:
    graph, scene = _get_scene_or_404(repo, project.id, ordinal)
    repo.save_shotlist(project.id, ordinal, body)
    findings = _run_validator(project, graph, scene, body)
    records = repo.replace_findings(project.id, ordinal, findings)
    return [_finding_out(r) for r in records]


@router.get("/shots", response_model=ShotListOut)
def get_shots(
    ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> ShotListOut:
    record = repo.get_shotlist(project.id, ordinal)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"No shot list for scene {ordinal}"
        )
    return ShotListOut(
        scene_ordinal=record.scene_ordinal,
        action_axis=record.shotlist.action_axis,
        shots=record.shotlist.shots,
    )


@router.patch("/findings/{finding_id}", response_model=FindingOut)
def patch_finding(
    ordinal: int,
    finding_id: str,
    patch: FindingPatch,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> FindingOut:
    existing = repo.get_finding(finding_id)
    if (
        existing is None
        or existing.project_id != project.id
        or existing.scene_ordinal != ordinal
    ):
        raise HTTPException(status_code=404, detail="Finding not found")
    updated = repo.update_finding(finding_id, patch.deliberate, patch.deliberate_note)
    assert updated is not None
    return _finding_out(updated)


async def _persist_wav(
    store: ObjectStore | None, project_id: str, ordinal: int, wav_bytes: bytes
) -> bytes:
    """Park a freshly rendered WAV in the bucket; return what the repo should hold.

    ``b""`` once the upload lands — the bucket is then the durable home and
    keeping a second copy in the container's memory is exactly the cost this
    exists to remove. ``wav_bytes`` unchanged when there is no bucket, and also
    when the upload fails: a storage outage must not destroy audio the user just
    paid a TTS provider to synthesize, so the render degrades to today's
    in-memory behaviour and says so in the log.
    """
    if store is None or not wav_bytes:
        return wav_bytes
    try:
        await store.upload(
            store.settings.audio_object(project_id, ordinal), wav_bytes, "audio/wav"
        )
    except ObjectStoreError as exc:
        logger.warning(
            "scene audio for project %s scene %s could not be uploaded to object "
            "storage (%s); keeping the bytes in the repository",
            project_id,
            ordinal,
            exc,
        )
        return wav_bytes
    return b""


@router.post("/render/audio", response_model=AudioRenderOut, status_code=201)
async def render_audio(
    ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    tts: TTSProvider = Depends(get_tts),
    store: ObjectStore | None = Depends(get_object_store),
) -> AudioRenderOut:
    # PRD rights gate: nothing renders without an attestation on file.
    if not project.rights_attested:
        raise HTTPException(
            status_code=403,
            detail="Rights not attested for this project; cannot render audio",
        )
    _, scene = _get_scene_or_404(repo, project.id, ordinal)

    estimated_cents = _estimate_cost_cents(scene, tts)
    try:
        governor.guard(project.cost_spent_cents, project.cost_cap_cents, estimated_cents)
    except governor.CostCapExceeded as exc:
        raise HTTPException(
            status_code=402,
            detail=(
                f"Cost cap exceeded: spent {exc.spent}c + estimated {exc.requested}c "
                f"would exceed cap {exc.cap}c"
            ),
        ) from exc

    # Per-scene knobs the timeline editor wrote; a re-render after an edit is
    # what makes that edit audible.
    settings = repo.get_render_settings(project.id, ordinal)
    result, timing = await render_scene_audio_with_timing(
        scene, _voice_map(scene), tts, settings=settings
    )
    project.cost_spent_cents += estimated_cents
    wav_bytes = await _persist_wav(store, project.id, ordinal, result.wav_bytes)
    repo.save_audio_render(
        project.id,
        ordinal,
        wav_bytes=wav_bytes,
        duration_ms=result.duration_ms,
        clip_count=result.clip_count,
        ambience_tags=result.ambience_tags,
        timing=timing,
    )
    return AudioRenderOut(
        scene_ordinal=ordinal,
        duration_ms=result.duration_ms,
        clip_count=result.clip_count,
        ambience_tags=result.ambience_tags,
    )


@router.get("/audio")
async def get_audio(
    ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    store: ObjectStore | None = Depends(get_object_store),
) -> Response:
    record = repo.get_audio_render(project.id, ordinal)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"No audio rendered yet for scene {ordinal}"
        )
    # An empty record means the render was uploaded rather than held here. The
    # object key is derived from (project, scene) rather than stored, because
    # AudioRenderRecord has nowhere to put it — see app.storage.settings.
    wav_bytes = record.wav_bytes
    if not wav_bytes:
        if store is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Scene {ordinal} audio lives in object storage, which is not "
                    "configured on this instance (set GCS_BUCKET)"
                ),
            )
        try:
            wav_bytes = await store.fetch(store.settings.audio_object(project.id, ordinal))
        except ObjectNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Rendered audio is no longer in object storage: {exc}",
            ) from exc
        except ObjectStoreError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not read the rendered audio from object storage: {exc}",
            ) from exc
    # The bytes are still the last real render, but a client that caches them
    # needs to know the IR moved underneath.
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={"X-Render-Stale": "true" if record.stale else "false"},
    )


def _visual_lane(
    timing: SceneTiming, shotlist: SceneShotList | None
) -> list[TimelineVisualClip]:
    """Project each shot onto the dialogue-clip timings its covers_lines span.

    A shot's clip runs from the earliest onset to the latest end of the placed
    clips whose line_ordinal it covers. Shots covering no placed line (e.g. lines
    that were skipped as non-spoken) are omitted, and shots stay in ordinal order.
    """
    if shotlist is None:
        return []
    spans = {clip.line_ordinal: (clip.start_ms, clip.duration_ms) for clip in timing.clips}
    lane: list[TimelineVisualClip] = []
    for shot in shotlist.shots:
        covered = [spans[ln] for ln in shot.covers_lines if ln in spans]
        if not covered:
            continue
        start_ms = min(start for start, _ in covered)
        end_ms = max(start + duration for start, duration in covered)
        lane.append(
            TimelineVisualClip(
                start_ms=start_ms,
                duration_ms=end_ms - start_ms,
                shot_ordinal=shot.ordinal,
                size=shot.size,
                subjects=list(shot.subjects),
            )
        )
    return lane


def _dialogue_lane(
    timing: SceneTiming, scene: NormalizedScene
) -> list[TimelineDialogueClip]:
    """Stored onsets, live line content.

    Onsets and durations can only come from a render pass, but speaker, emotion
    and text are IR facts an edit may have changed since. Reading them off the
    scene means an attribution fix shows on the timeline immediately, while the
    render's ``stale`` flag carries the (true) news that the WAV still has the
    old voice. Lines the render knew but the IR no longer has fall back to the
    render snapshot.
    """
    lines = {line.ordinal: line for line in scene.lines}
    lane: list[TimelineDialogueClip] = []
    for clip in timing.clips:
        line = lines.get(clip.line_ordinal)
        if line is None:
            character, emotion, text = clip.character_name, clip.emotion, clip.text
        else:
            # Mirror the renderer's rule: only dialogue carries a speaker.
            character = line.character_name if line.kind == "dialogue" else None
            emotion, text = line.emotion, line.text
        lane.append(
            TimelineDialogueClip(
                line_ordinal=clip.line_ordinal,
                start_ms=clip.start_ms,
                duration_ms=clip.duration_ms,
                character=character,
                emotion=emotion,
                text=text,
            )
        )
    return lane


def _build_timeline(
    record: AudioRenderRecord,
    scene: NormalizedScene,
    shotlist: SceneShotList | None,
    settings: SceneRenderSettings,
) -> SceneTimeline:
    timing = record.timing
    dialogue = _dialogue_lane(timing, scene)
    ambience = [
        TimelineAmbienceSpan(start_ms=0, duration_ms=timing.duration_ms, tag=tag)
        for tag in timing.ambience_tags
    ]
    sfx = [TimelineSfxMarker(at_ms=marker.at_ms, name=marker.name) for marker in timing.sfx]
    return SceneTimeline(
        scene_ordinal=timing.scene_ordinal,
        duration_ms=timing.duration_ms,
        markers=[
            SceneMarker(scene_ordinal=scene.ordinal, start_ms=0, slugline=scene.slugline)
        ],
        dialogue=dialogue,
        ambience=ambience,
        sfx=sfx,
        visual=_visual_lane(timing, shotlist),
        settings=settings,
        stale=record.stale,
        stale_reasons=list(record.stale_reasons),
    )


def _unrendered_timeline(
    scene: NormalizedScene, settings: SceneRenderSettings
) -> SceneTimeline:
    """A scene with no render yet: the marker and the settings, no lanes.

    Lane timings exist only as a product of a render pass, so there is nothing
    honest to put in them before one has run.
    """
    return SceneTimeline(
        scene_ordinal=scene.ordinal,
        duration_ms=0,
        markers=[
            SceneMarker(scene_ordinal=scene.ordinal, start_ms=0, slugline=scene.slugline)
        ],
        dialogue=[],
        ambience=[],
        sfx=[],
        visual=[],
        settings=settings,
    )


@router.get("/timeline", response_model=SceneTimeline)
def get_timeline(
    ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> SceneTimeline:
    _, scene = _get_scene_or_404(repo, project.id, ordinal)
    record = repo.get_audio_render(project.id, ordinal)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"No audio rendered yet for scene {ordinal}"
        )
    shotlist_record = repo.get_shotlist(project.id, ordinal)
    shotlist = shotlist_record.shotlist if shotlist_record is not None else None
    return _build_timeline(
        record, scene, shotlist, repo.get_render_settings(project.id, ordinal)
    )


@router.post("/timeline/edits", response_model=SceneTimeline)
def post_timeline_edits(
    ordinal: int,
    body: TimelineEditRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> SceneTimeline:
    """Apply timeline edits to the IR and return the timeline they produce.

    This is the editing surface of the render tier, so it takes the same rights
    attestation the render endpoints do. No cost governor call: edits are pure
    IR mutations and reach no provider — the guard belongs on the re-render that
    follows.

    A scene with no render yet still accepts edits (fix attribution first,
    render once); it just has no lanes to hand back, so unlike GET /timeline
    this does not 404 — the edits really were applied.
    """
    if not project.rights_attested:
        raise HTTPException(
            status_code=403,
            detail="Rights not attested for this project; cannot edit the timeline",
        )
    # 404s a missing scene before anything is touched; the edited copy of the
    # scene comes back out of the result below.
    graph, _scene = _get_scene_or_404(repo, project.id, ordinal)
    shotlist_record = repo.get_shotlist(project.id, ordinal)
    shotlist = shotlist_record.shotlist if shotlist_record is not None else None
    settings = repo.get_render_settings(project.id, ordinal)

    try:
        result = apply_edits(graph, ordinal, shotlist, settings, body.edits)
    except TimelineEditError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"edit {exc.index}: {exc}" if exc.index is not None else str(exc),
        ) from exc

    if result.graph_changed:
        repo.update_graph(project.id, result.graph)
    if result.shotlist_changed and result.shotlist is not None:
        repo.save_shotlist(project.id, ordinal, result.shotlist)
    if result.settings_changed:
        repo.save_render_settings(project.id, ordinal, result.settings)
    if result.invalidates_audio:
        repo.mark_audio_render_stale(project.id, ordinal, result.stale_reasons)

    edited_scene = next(s for s in result.graph.scenes if s.ordinal == ordinal)
    if result.shotlist_changed and result.shotlist is not None:
        # Shot ordinals may have shifted under an insert, so the stored findings
        # no longer point where they claim: re-derive them (continuity warns,
        # never blocks, so this cannot fail the edit).
        findings = _run_validator(project, result.graph, edited_scene, result.shotlist)
        repo.replace_findings(project.id, ordinal, findings)

    record = repo.get_audio_render(project.id, ordinal)
    if record is None:
        return _unrendered_timeline(edited_scene, result.settings)
    return _build_timeline(record, edited_scene, result.shotlist, result.settings)
