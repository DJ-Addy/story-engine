"""Script upload, story graph retrieval, and manual line attribution patches."""

from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.api.deps import get_owned_project, get_repo
from app.api.repo import ProjectRecord, Repository
from app.api.schemas import LinePatch, ScriptUploadOut, StoryGraphOut
from app.ingest.elements import AttributedLine, StoryGraph
from app.ingest.fdx import parse_fdx
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize

router = APIRouter(prefix="/projects/{project_id}", tags=["scripts"])

_PARSERS = {
    ".fountain": ("fountain", parse_fountain),
    ".txt": ("fountain", parse_fountain),
    ".fdx": ("fdx", parse_fdx),
}


def _get_graph_or_404(repo: Repository, project_id: str) -> StoryGraph:
    script = repo.get_script(project_id)
    if script is None:
        raise HTTPException(status_code=404, detail="No script uploaded yet")
    return script.graph


@router.post("/script", response_model=ScriptUploadOut, status_code=201)
async def upload_script(
    file: UploadFile,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> ScriptUploadOut:
    suffix = PurePosixPath(file.filename or "").suffix.lower()
    if suffix not in _PARSERS:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported script format {suffix or '(none)'}; "
            "expected .fountain, .txt, or .fdx",
        )
    fmt, parser = _PARSERS[suffix]
    text = (await file.read()).decode("utf-8", errors="replace")
    try:
        elements = parser(text)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Could not parse {fmt} script") from exc

    graph = normalize(elements)
    script = repo.save_script(project.id, fmt, graph)
    return ScriptUploadOut(
        script_id=script.id,
        format=fmt,
        scene_count=len(graph.scenes),
        character_count=len(graph.characters),
    )


@router.get("/graph", response_model=StoryGraphOut)
def get_graph(
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> StoryGraph:
    return _get_graph_or_404(repo, project.id)


@router.patch("/scenes/{ordinal}/lines/{line_ordinal}", response_model=AttributedLine)
def patch_line(
    ordinal: int,
    line_ordinal: int,
    patch: LinePatch,
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> AttributedLine:
    graph = _get_graph_or_404(repo, project.id)
    scene = next((s for s in graph.scenes if s.ordinal == ordinal), None)
    if scene is None:
        raise HTTPException(status_code=404, detail=f"Scene {ordinal} not found")
    line = next((l for l in scene.lines if l.ordinal == line_ordinal), None)
    if line is None:
        raise HTTPException(
            status_code=404, detail=f"Line {line_ordinal} not found in scene {ordinal}"
        )

    if patch.character_name is not None:
        line.character_name = patch.character_name
    if patch.text is not None:
        line.text = patch.text
    line.attribution_source = "manual"
    line.attribution_confidence = 1.0

    repo.update_graph(project.id, graph)
    return line
