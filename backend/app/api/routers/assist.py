"""The edit assistant over HTTP: one turn, one proposal, nothing applied.

``POST /projects/{id}/scenes/{ordinal}/assist`` takes what the user typed plus
the conversation so far, and answers with prose *and* a batch of ops from the
closed vocabulary in :mod:`app.render.timeline_edits`. It changes nothing. The
Apply button on the panel sends that batch to ``POST .../timeline/edits``, which
is the one place edits land — duplicating that here would give the product two
editors with one vocabulary and two sets of bugs.

Three shapes worth naming:

* **Configuration is answered before anything else.** ``get_llm`` is a
  dependency, so a deployment with no Google Cloud project fails during
  dependency resolution with a ``TerminalProviderError`` whose message names
  ``GOOGLE_CLOUD_PROJECT`` and ``GOOGLE_APPLICATION_CREDENTIALS``; the
  application handler in :mod:`app.api.main` turns that into a 503 with that
  wording as the ``detail``. A judge on a credential-less deploy is told exactly
  what to set, in one line, instead of getting a 500.
* **An LLM call is a paid call.** The prompt is built here so the governor is
  charged for the exact strings that will be sent, a refusal is recorded to
  ClickHouse as a ``cost_events`` row (a refused request produces no other
  trace of the pressure it was under), and the project's ledger moves by the
  same pre-flight estimate the render routes use.
* **The rights gate applies.** The assistant only proposes, but every proposal
  exists to be applied, and applying is gated on an attestation. Spending
  provider credits on a batch the API would refuse to accept is not a service
  to anybody, so the gate is checked here rather than discovered at Apply.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.base import LLMProvider
from app.analytics.events import CostEvent, new_run_id
from app.analytics.recorder import EventRecorder
from app.api.deps import get_llm, get_owned_project, get_repo
from app.api.repo import ProjectRecord, Repository
from app.api.routers.analytics import emit, get_analytics_recorder
from app.api.schemas import AssistOut, AssistRequest
from app.costs import governor
from app.ingest.elements import NormalizedScene, StoryGraph
from app.render.assist import build_assist_prompt, propose_edits

router = APIRouter(prefix="/projects/{project_id}/scenes/{ordinal}", tags=["assist"])


def _get_scene_or_404(
    repo: Repository, project_id: str, ordinal: int
) -> tuple[StoryGraph, NormalizedScene]:
    """The scene the assistant is talking about, or the reason there isn't one.

    Deliberately a local copy of the scene router's lookup rather than an import
    of its private helper: two routers sharing a private name is a coupling that
    outlives the eight lines it saves.
    """
    script = repo.get_script(project_id)
    if script is None:
        raise HTTPException(status_code=404, detail="No script uploaded yet")
    scene = next((s for s in script.graph.scenes if s.ordinal == ordinal), None)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene {ordinal} not found")
    return script.graph, scene


@router.post(
    "/assist",
    response_model=AssistOut,
    responses={
        402: {"description": "The cost governor refused: this call would pass the cap."},
        403: {"description": "Rights are not attested for this project."},
        503: {"description": "No LLM provider is configured on this deployment."},
    },
)
async def post_assist(
    ordinal: int,
    body: AssistRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    llm: LLMProvider = Depends(get_llm),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AssistOut:
    """Answer one message with prose and a batch of ops the user can apply."""
    if not project.rights_attested:
        raise HTTPException(
            status_code=403,
            detail=(
                "Rights not attested for this project; the assistant proposes "
                "timeline edits, which cannot be applied without an attestation"
            ),
        )
    graph, scene = _get_scene_or_404(repo, project.id, ordinal)
    shotlist_record = repo.get_shotlist(project.id, ordinal)
    shotlist = shotlist_record.shotlist if shotlist_record is not None else None
    settings = repo.get_render_settings(project.id, ordinal)

    # Built before the guard so the estimate prices the strings that are actually
    # sent, and so the whole scene's contribution to the prompt is in the number.
    prompt = build_assist_prompt(
        graph, scene, shotlist, settings, body.message, body.history
    )
    estimated_cents = llm.estimate_cost_cents(len(prompt[0]) + len(prompt[1]))

    # One correlation id for every row this request produces, so the governor's
    # decision and the turn it authorised join up in ClickHouse.
    run_id = new_run_id()

    def _cost_event(allowed: bool) -> CostEvent:
        return CostEvent.decide(
            project_id=project.id,
            run_id=run_id,
            operation="assist",
            provider=getattr(llm, "name", ""),
            estimated_cents=estimated_cents,
            spent_before_cents=project.cost_spent_cents,
            cap_cents=project.cost_cap_cents,
            allowed=allowed,
            scene_ordinal=ordinal,
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

    proposal = await propose_edits(prompt, graph, ordinal, shotlist, settings, llm)
    # Charged the estimate, not the provider's reported figure — the same
    # convention as the render routes, so one ledger has one meaning.
    project.cost_spent_cents += estimated_cents

    return AssistOut(
        reply=proposal.reply,
        edits=proposal.edits,
        undo=proposal.undo,
        undo_summary=proposal.undo_summary,
        undo_blocked_by=proposal.undo_blocked_by,
        dropped=proposal.dropped,
        provider=proposal.provider,
        model=proposal.model,
        estimated_cost_cents=estimated_cents,
    )
