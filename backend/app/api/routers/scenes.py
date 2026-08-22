"""Scene shot lists and continuity findings.

POST /shotlist is the manual authoring path for M1; the LLM shot-list
generator is a separate module and will feed the same endpoint contract.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_owned_project, get_repo
from app.api.repo import FindingRecord, ProjectRecord, Repository
from app.api.schemas import FindingOut, FindingPatch, ShotListOut
from app.continuity.model import Finding, SceneContext, ShotMeta
from app.continuity.validator import validate_scene
from app.ingest.elements import NormalizedScene, StoryGraph
from app.shotlist.schema import SceneShotList

router = APIRouter(prefix="/projects/{project_id}/scenes/{ordinal}", tags=["scenes"])


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
