"""Scene shot lists and continuity findings.

POST /shotlist is the manual authoring path for M1; the LLM shot-list
generator is a separate module and will feed the same endpoint contract.
"""

from __future__ import annotations

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
    TimelineSfxMarker,
    TimelineVisualClip,
)
from app.continuity.model import Finding, SceneContext, ShotMeta
from app.continuity.validator import validate_scene
from app.costs import governor
from app.ingest.elements import NormalizedScene, StoryGraph
from app.render.audio.model import SceneTiming
from app.render.audio.pipeline import render_scene_audio_with_timing
from app.shotlist.schema import SceneShotList

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


@router.post("/render/audio", response_model=AudioRenderOut, status_code=201)
async def render_audio(
    ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    tts: TTSProvider = Depends(get_tts),
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

    result, timing = await render_scene_audio_with_timing(scene, _voice_map(scene), tts)
    project.cost_spent_cents += estimated_cents
    repo.save_audio_render(
        project.id,
        ordinal,
        wav_bytes=result.wav_bytes,
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
def get_audio(
    ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> Response:
    record = repo.get_audio_render(project.id, ordinal)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"No audio rendered yet for scene {ordinal}"
        )
    return Response(content=record.wav_bytes, media_type="audio/wav")


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


def _build_timeline(
    record: AudioRenderRecord, scene: NormalizedScene, shotlist: SceneShotList | None
) -> SceneTimeline:
    timing = record.timing
    dialogue = [
        TimelineDialogueClip(
            line_ordinal=clip.line_ordinal,
            start_ms=clip.start_ms,
            duration_ms=clip.duration_ms,
            character=clip.character_name,
            emotion=clip.emotion,
            text=clip.text,
        )
        for clip in timing.clips
    ]
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
    return _build_timeline(record, scene, shotlist)
