"""Novel manuscript ingest: prose -> Fountain screenplay -> story graph.

Converted novels enter the render pipeline as first-class screenplays: the
Fountain text emitted by ``novel_to_screenplay`` is re-parsed through the
same ``parse_fountain`` + ``normalize`` chain used for uploaded screenplays
(see app.api.routers.scripts) and persisted via ``repo.save_script``. The
LLM repair pass is always skipped here (``llm=None``) so ingest stays
offline and deterministic; low-confidence quotes surface via
``needs_review`` for a future review UI instead.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from app.api.deps import get_owned_project, get_repo
from app.api.repo import ProjectRecord, Repository
from app.api.schemas import NovelIngestOut, NovelPreviewOut
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize
from app.ingest.novel import NovelConversionResult, novel_to_screenplay

router = APIRouter(prefix="/projects/{project_id}", tags=["novel"])


async def _convert(file: UploadFile, title: str | None, author: str | None) -> NovelConversionResult:
    suffix = PurePosixPath(file.filename or "").suffix.lower()
    if suffix != ".txt":
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported manuscript format {suffix or '(none)'}; expected .txt",
        )
    text = (await file.read()).decode("utf-8", errors="replace")
    return await novel_to_screenplay(text, llm=None, title=title, author=author)


@router.post("/novel", response_model=NovelIngestOut, status_code=201)
async def upload_novel(
    file: UploadFile,
    title: str | None = Form(None),
    author: str | None = Form(None),
    project: ProjectRecord = Depends(get_owned_project),
    repo: Repository = Depends(get_repo),
) -> NovelIngestOut:
    result = await _convert(file, title, author)

    elements = parse_fountain(result.fountain_text)
    graph = normalize(elements)
    script = repo.save_script(project.id, "fountain", graph)

    return NovelIngestOut(
        script_id=script.id,
        scene_count=len(graph.scenes),
        character_count=len(graph.characters),
        quotes=result.quotes,
        attributed=result.attributed,
        needs_review=result.needs_review,
        characters=result.characters,
    )


@router.post("/novel/preview", response_model=NovelPreviewOut, status_code=200)
async def preview_novel(
    file: UploadFile,
    title: str | None = Form(None),
    author: str | None = Form(None),
    project: ProjectRecord = Depends(get_owned_project),
) -> NovelPreviewOut:
    """Convert without persisting, so the UI can surface UNKNOWN SPEAKER /
    needs_review before the user commits the conversion."""
    result = await _convert(file, title, author)

    return NovelPreviewOut(
        quotes=result.quotes,
        attributed=result.attributed,
        needs_review=result.needs_review,
        characters=result.characters,
        fountain_text=result.fountain_text,
    )
