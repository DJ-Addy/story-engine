"""Storyboard ("animatic") frames: one still image per shot.

The first half of a two-step video pipeline. A board is cheap (cents, seconds)
where a Veo clip is not (dollars, minutes), and once a frame is stored the video
renderer switches to image-to-video on its own (``render_video`` looks for
``repo.get_shot_frame``). So boarding a scene first lets a user look at every
shot's composition before paying for motion, and the motion then matches what
they approved.

Gating mirrors ``render_video`` exactly, and in the same order: rights not
attested -> 403; no script/scene/shot list/shot -> 404; estimated cost over cap
-> 402, decided before the provider is ever invoked. Both the governor's
decision and the finished render are written to ClickHouse under one
``run_id`` for the same reason as there: a refusal produces no render row, so
cap pressure is invisible unless the decision itself is recorded.

The scene-wide route renders shots one at a time on purpose. Concurrent calls
would race the cost governor (every request sees the same ``cost_spent_cents``
and all of them pass), and a provider rate limit would then fail the whole
batch instead of the one shot it hit. Sequential is slower and correct. A
provider failure part-way through is re-raised, not swallowed: the frames
already rendered stay persisted (they were paid for and the next call will
skip them), and the global handlers turn the exception into a 502/503 the
client can act on.
"""

from __future__ import annotations

import hashlib
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Response

from app.adapters.base import ImageProvider
from app.analytics.events import CostEvent, RenderEvent, new_run_id
from app.analytics.recorder import EventRecorder
from app.api.deps import get_image, get_owned_project, get_repo
from app.api.repo import ProjectRecord, Repository
from app.api.routers.analytics import emit, get_analytics_recorder
from app.api.routers.renders import _get_scene_or_404, _get_shot_or_404, _shot_prompt
from app.api.schemas import (
    BoardRenderOut,
    SceneBoardsOut,
    SceneBoardsRequest,
    SceneBoardsStatus,
)
from app.costs import governor
from app.ingest.elements import NormalizedScene
from app.shotlist.schema import ShotSpec

router = APIRouter(prefix="/projects/{project_id}", tags=["boards"])

# Appended to the shot's video prompt: same subjects/size/setting so the board
# and the clip agree, plus the constraints that make a still usable as a
# storyboard frame. Burned-in text or watermarks would be carried straight
# into the image-to-video pass.
_BOARD_STYLE = (
    ", storyboard frame, cinematic composition, 16:9, no text, no captions, "
    "no watermark"
)


def _board_prompt(shot: ShotSpec, scene: NormalizedScene) -> str:
    return _shot_prompt(shot, scene) + _BOARD_STYLE


def _board_seed(project_id: str, scene_ordinal: int, shot_ordinal: int) -> int:
    """Stable per-shot seed so a re-render is a retry, not a new roll.

    Masked to 31 bits so it stays a valid int32 for any provider that does
    forward it to the API.
    """
    digest = hashlib.sha256(f"{project_id}|{scene_ordinal}|{shot_ordinal}".encode()).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFF_FFFF


def _require_rights(project: ProjectRecord) -> None:
    # PRD rights gate: nothing renders without an attestation on file.
    if not project.rights_attested:
        raise HTTPException(
            status_code=403,
            detail="Rights not attested for this project; cannot render boards",
        )


def _cap_exceeded(exc: governor.CostCapExceeded) -> HTTPException:
    return HTTPException(
        status_code=402,
        detail=(
            f"Cost cap exceeded: spent {exc.spent}c + estimated {exc.requested}c "
            f"would exceed cap {exc.cap}c"
        ),
    )


def _cost_event(
    project: ProjectRecord,
    image: ImageProvider,
    run_id: str,
    *,
    estimated_cents: int,
    allowed: bool,
    scene_ordinal: int,
    shot_ordinal: int = 0,
) -> CostEvent:
    return CostEvent.decide(
        project_id=project.id,
        run_id=run_id,
        operation="render_board",
        provider=getattr(image, "name", ""),
        estimated_cents=estimated_cents,
        spent_before_cents=project.cost_spent_cents,
        cap_cents=project.cost_cap_cents,
        allowed=allowed,
        scene_ordinal=scene_ordinal,
        shot_ordinal=shot_ordinal,
    )


async def _render_board(
    project: ProjectRecord,
    repo: Repository,
    image: ImageProvider,
    recorder: EventRecorder,
    scene: NormalizedScene,
    shot: ShotSpec,
    run_id: str,
) -> BoardRenderOut:
    """Governor -> provider -> persist -> events, for one shot.

    Shared by the single-shot and scene-wide routes so the two cannot drift in
    what they charge, store or record. The spend is booked only after the
    provider returns: a failed call costs the project nothing in the ledger,
    matching every other render path.
    """
    estimated_cents = image.estimate_cost_cents(1)
    try:
        governor.guard(project.cost_spent_cents, project.cost_cap_cents, estimated_cents)
    except governor.CostCapExceeded as exc:
        emit(
            recorder,
            [
                _cost_event(
                    project,
                    image,
                    run_id,
                    estimated_cents=estimated_cents,
                    allowed=False,
                    scene_ordinal=scene.ordinal,
                    shot_ordinal=shot.ordinal,
                )
            ],
        )
        raise _cap_exceeded(exc) from exc
    emit(
        recorder,
        [
            _cost_event(
                project,
                image,
                run_id,
                estimated_cents=estimated_cents,
                allowed=True,
                scene_ordinal=scene.ordinal,
                shot_ordinal=shot.ordinal,
            )
        ],
    )

    prompt = _board_prompt(shot, scene)
    seed = _board_seed(project.id, scene.ordinal, shot.ordinal)

    started = perf_counter()
    result = await image.generate(prompt, seed, {})
    latency_ms = int((perf_counter() - started) * 1000)

    project.cost_spent_cents += estimated_cents
    repo.save_shot_frame(project.id, scene.ordinal, shot.ordinal, result.image_bytes)
    emit(
        recorder,
        [
            RenderEvent(
                project_id=project.id,
                run_id=run_id,
                kind="board",
                scene_ordinal=scene.ordinal,
                shot_ordinal=shot.ordinal,
                provider=result.provider,
                model=result.model,
                source="text",
                status="ok",
                clip_count=1,
                cost_cents=result.cost_cents,
                estimated_cost_cents=estimated_cents,
                latency_ms=latency_ms,
            )
        ],
    )
    return BoardRenderOut(
        scene_ordinal=scene.ordinal,
        shot_ordinal=shot.ordinal,
        cost_cents=result.cost_cents,
        provider=result.provider,
        model=result.model,
        source="text",
    )


@router.post(
    "/scenes/{scene_ordinal}/shots/{shot_ordinal}/board",
    response_model=BoardRenderOut,
    status_code=201,
)
async def render_board(
    scene_ordinal: int,
    shot_ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    image: ImageProvider = Depends(get_image),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> BoardRenderOut:
    _require_rights(project)
    scene = _get_scene_or_404(repo, project.id, scene_ordinal)
    shot = _get_shot_or_404(repo, project.id, scene_ordinal, shot_ordinal)
    return await _render_board(project, repo, image, recorder, scene, shot, new_run_id())


@router.get("/scenes/{scene_ordinal}/shots/{shot_ordinal}/board")
async def get_board(
    scene_ordinal: int,
    shot_ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> Response:
    frame = repo.get_shot_frame(project.id, scene_ordinal, shot_ordinal)
    if frame is None:
        raise HTTPException(
            status_code=404,
            detail=f"No board rendered yet for scene {scene_ordinal} shot {shot_ordinal}",
        )
    return Response(content=frame, media_type="image/png")


@router.post(
    "/scenes/{scene_ordinal}/boards",
    response_model=SceneBoardsOut,
    status_code=201,
)
async def render_scene_boards(
    scene_ordinal: int,
    body: SceneBoardsRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    image: ImageProvider = Depends(get_image),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> SceneBoardsOut:
    _require_rights(project)
    scene = _get_scene_or_404(repo, project.id, scene_ordinal)
    record = repo.get_shotlist(project.id, scene_ordinal)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"No shot list for scene {scene_ordinal}"
        )

    # Idempotent by default: a shot that already has a frame is left alone, so
    # re-running the route after a mid-batch failure finishes the job instead
    # of paying for the same boards twice. ``force`` is the explicit "new take".
    pending: list[ShotSpec] = []
    skipped: list[int] = []
    for shot in record.shotlist.shots:
        has_frame = (
            repo.get_shot_frame(project.id, scene_ordinal, shot.ordinal) is not None
        )
        if has_frame and not body.force:
            skipped.append(shot.ordinal)
        else:
            pending.append(shot)

    run_id = new_run_id()

    # Whole-batch pre-flight, so a scene that cannot fit under the cap is
    # refused before any of it is bought. Without this the per-shot guard
    # would render k boards and then answer 402 for the (k+1)th, and a 402
    # that has already spent money is the worst of both. Recorded as one
    # refusal at the scene grain (shot 0); approvals are recorded per shot,
    # at the grain they are actually spent.
    if pending:
        batch_estimate = image.estimate_cost_cents(len(pending))
        try:
            governor.guard(project.cost_spent_cents, project.cost_cap_cents, batch_estimate)
        except governor.CostCapExceeded as exc:
            emit(
                recorder,
                [
                    _cost_event(
                        project,
                        image,
                        run_id,
                        estimated_cents=batch_estimate,
                        allowed=False,
                        scene_ordinal=scene_ordinal,
                    )
                ],
            )
            raise _cap_exceeded(exc) from exc

    rendered: list[BoardRenderOut] = []
    for shot in pending:
        rendered.append(
            await _render_board(project, repo, image, recorder, scene, shot, run_id)
        )

    return SceneBoardsOut(
        scene_ordinal=scene_ordinal,
        rendered=rendered,
        skipped=skipped,
        total_cost_cents=sum(board.cost_cents for board in rendered),
    )


@router.get("/scenes/{scene_ordinal}/boards", response_model=SceneBoardsStatus)
async def get_scene_boards(
    scene_ordinal: int,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> SceneBoardsStatus:
    """Which shots of the scene have a frame, and how many shots there are.

    Read-only and ungated: it spends nothing, so neither rights nor the
    governor apply. The pipeline page opens on this before anything has been
    boarded, which is why "no shot list yet" is an empty status rather than a
    404 — it is the normal starting state, not a mistake. Only a project with
    no script at all is an error, since it cannot have scenes to ask about.
    """
    if repo.get_script(project.id) is None:
        raise HTTPException(status_code=404, detail="No script uploaded yet")
    record = repo.get_shotlist(project.id, scene_ordinal)
    if record is None:
        return SceneBoardsStatus(scene_ordinal=scene_ordinal, rendered=[], total=0)
    rendered = sorted(
        shot.ordinal
        for shot in record.shotlist.shots
        if repo.get_shot_frame(project.id, scene_ordinal, shot.ordinal) is not None
    )
    return SceneBoardsStatus(
        scene_ordinal=scene_ordinal,
        rendered=rendered,
        total=len(record.shotlist.shots),
    )
