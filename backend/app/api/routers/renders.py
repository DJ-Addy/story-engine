"""Project-scoped media render endpoints beyond the per-scene audio path.

Currently the Runway-backed video renderer: it turns a single shot into a
moving clip. Rights + cost-governor gating mirror the scene audio endpoint
exactly (rights not attested -> 403; estimated cost over cap -> 402, checked
before the provider is ever invoked).

image-to-video vs text-to-video: the renderer prefers image-to-video when a
per-shot board/frame image exists for the shot (``repo.get_shot_frame``) and
otherwise falls back to text-to-video built from the shot's description. The
previz pipeline does not yet render board images into the repo (``board_prompt``
only assembles text prompts, and the animatic assembler works on abstract image
paths), so in practice this renders text-to-video today; the frame hook is ready
for when boards are produced.

Both the governor's decision and the finished render are written to ClickHouse
(:mod:`app.analytics.events`) under one ``run_id``. The refusal is recorded as
well as the approval, and that is the point: a 402 produces no render row at
all, so cap pressure would otherwise be invisible in the data. Estimated and
actual cost are both kept, which is the only way to tell later whether the
estimator the governor trusts is honest.
"""

from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from app.adapters.base import VideoProvider
from app.analytics.events import CostEvent, RenderEvent, new_run_id
from app.analytics.recorder import EventRecorder
from app.api.deps import get_owned_project, get_repo, get_video
from app.api.repo import ProjectRecord, Repository
from app.api.routers.analytics import emit, get_analytics_recorder
from app.api.schemas import VideoRenderOut, VideoRenderRequest
from app.costs import governor
from app.ingest.elements import NormalizedScene
from app.render.visual.prompts import SIZE_PHRASES
from app.shotlist.schema import ShotSpec

router = APIRouter(prefix="/projects/{project_id}", tags=["renders"])


def _get_scene_or_404(
    repo: Repository, project_id: str, scene_ordinal: int
) -> NormalizedScene:
    script = repo.get_script(project_id)
    if script is None:
        raise HTTPException(status_code=404, detail="No script uploaded yet")
    scene = next((s for s in script.graph.scenes if s.ordinal == scene_ordinal), None)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene {scene_ordinal} not found")
    return scene


def _get_shot_or_404(
    repo: Repository, project_id: str, scene_ordinal: int, shot_ordinal: int
) -> ShotSpec:
    record = repo.get_shotlist(project_id, scene_ordinal)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"No shot list for scene {scene_ordinal}"
        )
    shot = next((s for s in record.shotlist.shots if s.ordinal == shot_ordinal), None)
    if shot is None:
        raise HTTPException(
            status_code=404,
            detail=f"Shot {shot_ordinal} not found in scene {scene_ordinal}",
        )
    return shot


def _shot_prompt(shot: ShotSpec, scene: NormalizedScene) -> str:
    """Deterministic text-to-video prompt from the shot's intent/subjects/size
    and the scene's slugline setting."""
    parts: list[str] = [SIZE_PHRASES.get(shot.size, shot.size)]
    if shot.subjects:
        parts.append("of " + ", ".join(shot.subjects))
    if shot.intent:
        parts.append(shot.intent)
    setting = scene.slugline or scene.location
    if setting:
        parts.append(f"at {setting}")
    if scene.time_of_day:
        parts.append(scene.time_of_day)
    return ", ".join(parts)


@router.post("/render/video", response_model=VideoRenderOut, status_code=201)
async def render_video(
    body: VideoRenderRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    video: VideoProvider = Depends(get_video),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> VideoRenderOut:
    # PRD rights gate: nothing renders without an attestation on file.
    if not project.rights_attested:
        raise HTTPException(
            status_code=403,
            detail="Rights not attested for this project; cannot render video",
        )
    scene = _get_scene_or_404(repo, project.id, body.scene_ordinal)
    shot = _get_shot_or_404(repo, project.id, body.scene_ordinal, body.shot_ordinal)

    # One correlation id for every row this request produces, so the governor's
    # decision and the render it authorised join up in ClickHouse.
    run_id = new_run_id()
    estimated_cents = video.estimate_cost_cents(body.duration_s)

    def _cost_event(allowed: bool) -> CostEvent:
        return CostEvent.decide(
            project_id=project.id,
            run_id=run_id,
            operation="render_video",
            provider=getattr(video, "name", ""),
            estimated_cents=estimated_cents,
            spent_before_cents=project.cost_spent_cents,
            cap_cents=project.cost_cap_cents,
            allowed=allowed,
            scene_ordinal=body.scene_ordinal,
            shot_ordinal=body.shot_ordinal,
        )

    try:
        governor.guard(project.cost_spent_cents, project.cost_cap_cents, estimated_cents)
    except governor.CostCapExceeded as exc:
        emit(recorder, [_cost_event(allowed=False)])
        raise HTTPException(
            status_code=402,
            detail=(
                f"Cost cap exceeded: spent {exc.spent}c + estimated {exc.requested}c "
                f"would exceed cap {exc.cap}c"
            ),
        ) from exc
    emit(recorder, [_cost_event(allowed=True)])

    prompt = _shot_prompt(shot, scene)
    frame = repo.get_shot_frame(project.id, body.scene_ordinal, body.shot_ordinal)
    started = perf_counter()
    if frame is not None:
        result = await video.generate(prompt, image=frame, duration_s=body.duration_s)
        source = "image"
    else:
        result = await video.generate(prompt, duration_s=body.duration_s)
        source = "text"
    latency_ms = int((perf_counter() - started) * 1000)

    project.cost_spent_cents += estimated_cents
    repo.save_video_render(
        project.id,
        body.scene_ordinal,
        body.shot_ordinal,
        video_bytes=result.video_bytes,
        output_urls=result.output_urls,
        duration_ms=result.duration_ms,
        cost_cents=result.cost_cents,
        provider=result.provider,
        model=result.model,
        source=source,
    )
    emit(
        recorder,
        [
            RenderEvent(
                project_id=project.id,
                run_id=run_id,
                kind="video",
                scene_ordinal=body.scene_ordinal,
                shot_ordinal=body.shot_ordinal,
                provider=result.provider,
                model=result.model,
                source=source,
                status="ok",
                duration_ms=result.duration_ms,
                clip_count=len(result.output_urls) or (1 if result.video_bytes else 0),
                cost_cents=result.cost_cents,
                estimated_cost_cents=estimated_cents,
                latency_ms=latency_ms,
            )
        ],
    )
    return VideoRenderOut(
        scene_ordinal=body.scene_ordinal,
        shot_ordinal=body.shot_ordinal,
        duration_ms=result.duration_ms,
        cost_cents=result.cost_cents,
        provider=result.provider,
        model=result.model,
        source=source,
        output_urls=result.output_urls,
        has_video=bool(result.video_bytes),
    )


@router.get("/render/video/{scene_ordinal}/{shot_ordinal}")
def get_video_clip(
    scene_ordinal: int,
    shot_ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> Response:
    record = repo.get_video_render(project.id, scene_ordinal, shot_ordinal)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No video rendered yet for scene {scene_ordinal} shot {shot_ordinal}",
        )
    # Serve the downloaded clip when present, else hand back the provider URL(s).
    if record.video_bytes:
        return Response(content=record.video_bytes, media_type="video/mp4")
    return JSONResponse(
        content={
            "output_urls": record.output_urls,
            "provider": record.provider,
            "model": record.model,
            "duration_ms": record.duration_ms,
        }
    )
