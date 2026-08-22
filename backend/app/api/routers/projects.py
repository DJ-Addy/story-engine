"""Project endpoints with owner-scoped access."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user, get_owned_project, get_repo
from app.api.repo import ProjectRecord, Repository, UserRecord
from app.api.schemas import ProjectCreate, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


def _to_out(project: ProjectRecord) -> ProjectOut:
    return ProjectOut(**project.model_dump())


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    body: ProjectCreate,
    user: UserRecord = Depends(get_current_user),
    repo: Repository = Depends(get_repo),
) -> ProjectOut:
    # PRD: rights attestation is required before anything renders.
    if not body.rights_attested:
        raise HTTPException(
            status_code=422,
            detail="rights_attested must be true before a project can be created",
        )
    project = repo.create_project(
        owner_id=user.id,
        title=body.title,
        grammar_profile=body.grammar_profile,
        validator_mode=body.validator_mode,
        rights_attested=body.rights_attested,
    )
    return _to_out(project)


@router.get("", response_model=list[ProjectOut])
def list_projects(
    user: UserRecord = Depends(get_current_user),
    repo: Repository = Depends(get_repo),
) -> list[ProjectOut]:
    return [_to_out(p) for p in repo.list_projects(user.id)]


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project: ProjectRecord = Depends(get_owned_project)) -> ProjectOut:
    return _to_out(project)
