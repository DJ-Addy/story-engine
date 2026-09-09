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

from fastapi import APIRouter, Depends, HTTPException, Response

from app.adapters.base import Voice
from app.analytics.events import animatic_events, ranking_events, voice_fit_events
from app.analytics.recorder import EventRecorder
from app.api.deps import get_owned_project, get_repo
from app.api.repo import CastEntry, CastingRecord, ProjectRecord, Repository
from app.api.routers.analytics import emit, get_analytics_recorder
from app.api.schemas import (
    CastingOverrideRequest,
    AnimaticJudgmentOut,
    AnimaticRankingOut,
    AnimaticRankRequest,
    CastingDecisionRequest,
    VoiceFitOut,
    VoiceFitRequest,
    VoiceRankingOut,
    VoiceRankRequest,
)
from app.ingest.elements import StoryGraph
from app.judge import (
    CastingProposal,
    judge_animatic,
    judge_voice_fit,
    propose_casting_with_tone,
    rank_animatics,
    rank_voice_fits,
)
from app.judge.model import AnimaticJudgment, VoiceFitResult
from app.judge.scorecard import render_full_scorecard, scorecard_filename
from app.shotlist.schema import SceneShotList

router = APIRouter(prefix="/projects/{project_id}/judge", tags=["judge"])


def _get_graph_or_404(repo: Repository, project_id: str) -> StoryGraph:
    script = repo.get_script(project_id)
    if script is None:
        raise HTTPException(status_code=404, detail="No script uploaded yet")
    return script.graph


@router.put("/casting", response_model=CastingProposal)
def override_casting(
    body: CastingOverrideRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> CastingProposal:
    """Replace the casting with the director's own.

    The judge proposes; this is how someone overrules it. It exists because a
    proposal made without an LLM cannot know things the text never states -
    that Ulysses is a man, that the Sirens are women - so a heuristic that
    deals voices by line count will sometimes be confidently wrong about a
    part, and the fix should not be to make the heuristic pretend it knows.

    Stored with ``source="manual"``, so a later reader can tell a decision that
    was made from one that was proposed. Validated against the story graph:
    casting a character the script does not have is a typo, and silently
    keeping it would leave a casting the renderer never consults.
    """
    graph = _get_graph_or_404(repo, project.id)
    known = {
        line.character_name
        for scene in graph.scenes
        for line in scene.lines
        if line.character_name
    }
    unknown = sorted(
        {e.character for e in body.entries if e.character is not None} - known
    )
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No such character(s) in this script: {', '.join(unknown)}. "
                f"Known: {', '.join(sorted(known)) or '(none)'}"
            ),
        )

    record = repo.save_casting(
        project.id,
        [
            CastEntry(
                character=e.character,
                voice_id=e.voice_id,
                voice_name=e.voice_name or e.voice_id,
                tone=e.tone,
                confidence=1.0,  # a person decided; there is nothing to estimate
                rationale=e.rationale or "Cast by the director.",
                chorus_voice_ids=list(e.chorus_voice_ids),
            )
            for e in body.entries
        ],
        "manual",
    )
    return _casting_to_proposal(record)


@router.post("/casting", response_model=CastingProposal, status_code=201)
async def decide_casting(
    body: CastingDecisionRequest,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> CastingProposal:
    """Read the script and decide a voice AND a tone for every speaker.

    This is the difference between scoring a casting and making one.
    ``POST /judge/voices`` grades a casting the caller already has; this reads
    the lines themselves - who speaks how often, which parentheticals the writer
    left, how the dialogue is punctuated - and returns a decision, with the
    evidence it used named in each rationale.

    The result is persisted by default, because a decision nothing records is
    the state this endpoint exists to end: the renderer reads the saved casting,
    so proposing without saving would leave the audio unchanged. ``persist:
    false`` makes it a dry run for a UI that wants to preview before committing.
    """
    graph = _get_graph_or_404(repo, project.id)

    voices = body.available_voices
    if voices is None:
        # Resolved lazily and only when needed, so a caller who supplies the
        # pool keeps this endpoint credential-free like every other judge.
        from app.adapters.base import TerminalProviderError
        from app.api.deps import get_tts

        try:
            voices = await get_tts().list_voices()
        except TerminalProviderError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"{exc} - or pass available_voices in the request body to "
                    "cast without a provider configured"
                ),
            ) from exc

    try:
        proposal = propose_casting_with_tone(graph, voices)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if body.persist:
        repo.save_casting(
            project.id,
            [
                CastEntry(
                    character=entry.character,
                    voice_id=entry.voice_id,
                    voice_name=entry.voice_name,
                    tone=entry.tone,
                    confidence=entry.confidence,
                    rationale=entry.rationale,
                )
                for entry in proposal.entries
            ],
            "judge",
        )
    return proposal


def _casting_to_proposal(record: CastingRecord) -> CastingProposal:
    """Render a stored casting in the same shape the proposer returns.

    A saved record keeps the decision but not the arithmetic behind it, so
    ``voice_fit`` comes back 0.0 - see the scorecard, which reads that as
    "restored from storage" rather than printing it as a score.
    """
    from app.judge.model import CastingProposalEntry

    return CastingProposal(
        entries=[
            CastingProposalEntry(
                character=entry.character,
                is_narrator=entry.character is None,
                voice_id=entry.voice_id,
                voice_name=entry.voice_name,
                tone=entry.tone,
                confidence=entry.confidence,
                voice_fit=0.0,
                chorus_voice_ids=list(entry.chorus_voice_ids),
                rationale=entry.rationale,
            )
            for entry in record.entries
        ],
        rationale=f"Saved casting ({record.source}).",
    )


@router.get("/casting", response_model=CastingProposal | None)
def get_decided_casting(
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> CastingProposal | None:
    """The casting this project renders with, or null if none was decided."""
    record = repo.get_casting(project.id)
    return None if record is None else _casting_to_proposal(record)


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


# --------------------------------------------------------------------------- #
# Scorecard: the same judgements, written out for a person
# --------------------------------------------------------------------------- #
# A saved casting is stored as app.api.repo.CastEntry, which keeps the voice's
# id and name but not the provider tags it was chosen on — the catalogue those
# came from is behind credentials this endpoint deliberately does not use. So
# the fit is scored against tag-neutral voices, and the scorecard says so in
# the reader's own words rather than passing a hollowed-out number off as a
# tag-aware one.
_TAGLESS_VOICES_NOTE = (
    "One caveat on the numbers in this section: they were scored from the "
    "casting as it is saved, which records each voice's id and name but not "
    "the provider tags it was originally chosen on. The voice side of every "
    "comparison therefore reads as tag-neutral, and what moves these scores is "
    "the character side — line counts, delivery emotions, and how much of a "
    "part is narration. For the tag-aware figure, re-score the casting through "
    "POST /judge/voices with the provider's available_voices in the body."
)


async def _scorecard_text(project: ProjectRecord, repo: Repository) -> str:
    """Assemble whatever this project has been judged on, as plain text.

    Deliberately not an all-or-nothing report: a project that has been cast but
    never shot-listed is the normal state halfway through a session, and a 404
    there would tell a reviewer nothing about the half that *is* done. The only
    hard failure is a project with no script, because then not one of the three
    judges has anything to read.

    No analytics row is written. The judgements here are recomputed from stored
    material rather than newly decided, so emitting would inflate the ClickHouse
    counts every time somebody refreshed the page.
    """
    graph = _get_graph_or_404(repo, project.id)

    # The saved casting, read exactly as GET /judge/casting reports it, so the
    # page and the API can never disagree about what this project renders with.
    casting = get_decided_casting(project=project, repo=repo)

    voice_fit: VoiceFitResult | None = None
    note: str | None = None
    if casting is not None:
        cast_voices = {
            entry.character: Voice(id=entry.voice_id, name=entry.voice_name, tags=[])
            for entry in casting.entries
            if entry.character is not None
        }
        if cast_voices:
            # available_voices stays None so suggestions are drawn only from
            # voices this casting actually uses: recommending a recast to a
            # voice we cannot see the tags of would be advice with no evidence.
            voice_fit = await judge_voice_fit(graph, cast_voices, llm=None)
            note = _TAGLESS_VOICES_NOTE

    shotlists: list[SceneShotList] = []
    for scene in graph.scenes:
        record = repo.get_shotlist(project.id, scene.ordinal)
        if record is not None:
            shotlists.append(record.shotlist)
    animatic: AnimaticJudgment | None = (
        judge_animatic(graph, shotlists, grammar_profile=project.grammar_profile)
        if shotlists
        else None
    )

    return render_full_scorecard(
        project_title=project.title,
        casting=casting,
        voice_fit=voice_fit,
        animatic=animatic,
        voice_fit_note=note,
    )


@router.get("/scorecard")
async def get_scorecard(
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> Response:
    """Every judge score this project has, explained in prose, as text/plain.

    One URL a reviewer can open to see how each number was arrived at: what it
    was computed from, and the judge's own sentence about it.
    """
    return Response(
        content=await _scorecard_text(project, repo),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/scorecard.txt")
async def download_scorecard(
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> Response:
    """The same page, offered as a file so a reviewer can keep a copy.

    Byte-for-byte the body of ``GET /judge/scorecard`` (bar the timestamp in
    its header); only the ``Content-Disposition`` differs, because a reviewer
    who wants the scorecard in their notes and one who wants it on screen are
    reading the same document.
    """
    filename = scorecard_filename(project.title)
    return Response(
        content=await _scorecard_text(project, repo),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
