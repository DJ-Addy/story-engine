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

**Where the clip lives.** A container's filesystem is ephemeral and its memory
is not free, so when a bucket is configured (:mod:`app.storage`) the render
prefers *provider-side* delivery: Veo is handed a ``storage_uri`` and writes the
MP4 straight into GCS, which never routes the bytes through this process at all.
A provider that returns bytes anyway gets them parked in the same bucket after
the fact. Either way the repository ends up holding ``gs://`` URIs instead of
megabytes, and the serve endpoint reads them back on demand. With no bucket
configured none of this happens and the bytes stay exactly where they are today.
"""

from __future__ import annotations

import logging
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import JSONResponse

from app.adapters.base import VideoProvider, VideoResult
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
from app.storage import ObjectNotFound, ObjectStore, ObjectStoreError, get_object_store

logger = logging.getLogger(__name__)

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


async def _persist_clip(
    store: ObjectStore | None,
    project_id: str,
    scene_ordinal: int,
    shot_ordinal: int,
    result: VideoResult,
) -> tuple[bytes, list[str]]:
    """Decide what the repository should hold for this clip.

    Returns ``(video_bytes, output_urls)``. With no bucket, or when the upload
    fails, that is exactly what the provider returned — the bytes are still the
    render the user just paid for and dropping them to punish a storage outage
    would be the worse failure. With a bucket, an inline clip is parked in it and
    the repository keeps only the ``gs://`` URI, which is the whole point: the
    container stops being the only copy *and* stops being a copy at all.

    A provider that already delivered to GCS (empty bytes, ``gs://`` URLs) needs
    nothing done to it.
    """
    video_bytes = result.video_bytes
    output_urls = list(result.output_urls)
    if store is None or not video_bytes:
        return video_bytes, output_urls

    name = store.settings.video_object(project_id, scene_ordinal, shot_ordinal)
    try:
        uri = await store.upload(name, video_bytes, "video/mp4")
    except ObjectStoreError as exc:
        logger.warning(
            "video render for project %s scene %s shot %s could not be uploaded "
            "to object storage (%s); keeping the bytes in the repository",
            project_id,
            scene_ordinal,
            shot_ordinal,
            exc,
        )
        return video_bytes, output_urls
    if uri not in output_urls:
        output_urls.append(uri)
    return b"", output_urls


@router.post("/render/video", response_model=VideoRenderOut, status_code=201)
async def render_video(
    body: VideoRenderRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    video: VideoProvider = Depends(get_video),
    recorder: EventRecorder = Depends(get_analytics_recorder),
    store: ObjectStore | None = Depends(get_object_store),
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

    kwargs: dict = {"duration_s": body.duration_s}
    source = "text"
    if frame is not None:
        kwargs["image"] = frame
        source = "image"
    if store is not None:
        # Provider-side delivery: Veo writes the finished MP4 into the bucket
        # itself and reports gs:// URIs, so the clip never occupies this
        # container's memory at all. Only added when a bucket exists, so the
        # unconfigured call is byte-for-byte the one made before.
        kwargs["params"] = {
            "storage_uri": store.settings.video_prefix_uri(
                project.id, body.scene_ordinal, body.shot_ordinal
            )
        }

    started = perf_counter()
    result = await video.generate(prompt, **kwargs)
    latency_ms = int((perf_counter() - started) * 1000)

    project.cost_spent_cents += estimated_cents
    video_bytes, output_urls = await _persist_clip(
        store, project.id, body.scene_ordinal, body.shot_ordinal, result
    )
    repo.save_video_render(
        project.id,
        body.scene_ordinal,
        body.shot_ordinal,
        video_bytes=video_bytes,
        output_urls=output_urls,
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
        output_urls=output_urls,
        # "the GET will hand you the clip" — true both when we still hold the
        # bytes and when they are one bucket read away.
        has_video=bool(video_bytes) or any(url.startswith("gs://") for url in output_urls),
    )


@router.get("/render/video/{scene_ordinal}/{shot_ordinal}")
async def get_video_clip(
    scene_ordinal: int,
    shot_ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    store: ObjectStore | None = Depends(get_object_store),
) -> Response:
    record = repo.get_video_render(project.id, scene_ordinal, shot_ordinal)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No video rendered yet for scene {scene_ordinal} shot {shot_ordinal}",
        )
    # Serve the downloaded clip when present, else read it back out of the
    # bucket it was delivered to, else hand back the provider URL(s).
    if record.video_bytes:
        return Response(content=record.video_bytes, media_type="video/mp4")

    gcs_uris = [url for url in record.output_urls if url.startswith("gs://")]
    if store is not None and gcs_uris:
        try:
            content = await store.fetch_uri(gcs_uris[0])
        except ObjectNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Rendered clip is no longer in object storage: {exc}",
            ) from exc
        except ObjectStoreError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Could not read the rendered clip from object storage: {exc}",
            ) from exc
        return Response(content=content, media_type="video/mp4")

    return JSONResponse(
        content={
            "output_urls": record.output_urls,
            "provider": record.provider,
            "model": record.model,
            "duration_ms": record.duration_ms,
        }
    )
