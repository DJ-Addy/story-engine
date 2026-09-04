"""AI judge endpoints: voice-fit casting and animatic / shot-list quality.

Both judges read the project's persisted story graph (and, for the animatic
judge, its stored shot lists) and return structured, actionable evaluations.
Scoring is the deterministic heuristic in :mod:`app.judge`; no LLM is wired in
here, so these endpoints never touch the network or spend credits (the judge
functions expose an optional ``llm`` seam for callers who want richer rubric
notes, mirroring how the novel router keeps ingest offline with ``llm=None``).

Every judgement is also fanned out into ClickHouse rows
(:mod:`app.analytics.events`) before the response is returned. That happens here
rather than inside :mod:`app.judge` on purpose: the judge is a pure scoring
function with no I/O, and the project a judgement belongs to is a fact of the
request, not of the scoring. The write is buffered and non-fatal, so it costs
the response nothing and cannot fail it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.analytics.events import animatic_events, ranking_events, voice_fit_events
from app.analytics.recorder import EventRecorder
from app.api.deps import get_owned_project, get_repo
from app.api.repo import ProjectRecord, Repository
from app.api.routers.analytics import emit, get_analytics_recorder
from app.api.schemas import (
    AnimaticJudgmentOut,
    AnimaticRankingOut,
    AnimaticRankRequest,
    VoiceFitOut,
    VoiceFitRequest,
    VoiceRankingOut,
    VoiceRankRequest,
)
from app.ingest.elements import StoryGraph
from app.judge import judge_animatic, judge_voice_fit, rank_animatics, rank_voice_fits
from app.shotlist.schema import SceneShotList

router = APIRouter(prefix="/projects/{project_id}/judge", tags=["judge"])


def _get_graph_or_404(repo: Repository, project_id: str) -> StoryGraph:
    script = repo.get_script(project_id)
    if script is None:
        raise HTTPException(status_code=404, detail="No script uploaded yet")
    return script.graph


@router.post("/voices", response_model=VoiceFitOut)
async def judge_voices(
    body: VoiceFitRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> VoiceFitOut:
    graph = _get_graph_or_404(repo, project.id)
    result = await judge_voice_fit(
        graph, body.casting, available_voices=body.available_voices, llm=None
    )
    # One row for the run plus one per character: the repo keeps only the latest
    # casting, so this is the only record that this variant was ever tried.
    emit(recorder, voice_fit_events(project.id, result))
    return VoiceFitOut(**result.model_dump())


@router.post("/animatic", response_model=AnimaticJudgmentOut)
def judge_animatic_endpoint(
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnimaticJudgmentOut:
    graph = _get_graph_or_404(repo, project.id)
    shotlists: list[SceneShotList] = []
    for scene in graph.scenes:
        record = repo.get_shotlist(project.id, scene.ordinal)
        if record is not None:
            shotlists.append(record.shotlist)
    if not shotlists:
        raise HTTPException(
            status_code=404, detail="No shot lists to judge; author a shot list first"
        )
    result = judge_animatic(graph, shotlists, grammar_profile=project.grammar_profile)
    emit(recorder, animatic_events(project.id, result, grammar_profile=project.grammar_profile))
    return AnimaticJudgmentOut(**result.model_dump())


@router.post("/rank/voices", response_model=VoiceRankingOut)
async def rank_voices(
    body: VoiceRankRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> VoiceRankingOut:
    graph = _get_graph_or_404(repo, project.id)
    result = await rank_voice_fits(
        graph, body.candidates, available_voices=body.available_voices, llm=None
    )
    # Every candidate under one run_id, so the leaderboard can be reassembled
    # later with a GROUP BY rather than inferred from timestamps.
    emit(recorder, ranking_events(project.id, result, judge="voice_fit"))
    return VoiceRankingOut(**result.model_dump())


@router.post("/rank/animatic", response_model=AnimaticRankingOut)
def rank_animatic_endpoint(
    body: AnimaticRankRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
    recorder: EventRecorder = Depends(get_analytics_recorder),
) -> AnimaticRankingOut:
    graph = _get_graph_or_404(repo, project.id)
    result = rank_animatics(
        graph, body.candidates, grammar_profile=project.grammar_profile
    )
    emit(
        recorder,
        ranking_events(
            project.id,
            result,
            judge="animatic",
            grammar_profile=project.grammar_profile,
        ),
    )
    return AnimaticRankingOut(**result.model_dump())
