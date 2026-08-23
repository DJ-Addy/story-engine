"""FastAPI dependencies: repository access and authentication."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.adapters.base import TTSProvider
from app.api import auth
from app.api.repo import InMemoryRepository, ProjectRecord, Repository, UserRecord

# Module-level singleton so all requests share state; tests override get_repo
# with a fresh InMemoryRepository via app.dependency_overrides.
_repo: Repository = InMemoryRepository()

_bearer = HTTPBearer(auto_error=False)


def get_repo() -> Repository:
    return _repo


def get_tts() -> TTSProvider:
    """Default TTS provider, selected from the environment.

    With AZURE_SPEECH_KEY + AZURE_SPEECH_REGION set, use the Azure neural
    adapter (real emotion via SSML express-as); otherwise fall back to the
    free, keyless Edge adapter. Imported lazily so offline test collection
    never pulls in the aiohttp network stack; tests always override this with
    app.adapters.fake.FakeTTS via app.dependency_overrides.
    """
    import os

    if os.environ.get("AZURE_SPEECH_KEY") and os.environ.get("AZURE_SPEECH_REGION"):
        from app.adapters.azure_tts import AzureTTSAdapter

        return AzureTTSAdapter()

    from app.adapters.edge import EdgeTTSAdapter

    return EdgeTTSAdapter()


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    repo: Repository = Depends(get_repo),
) -> UserRecord:
    unauthorized = HTTPException(
        status_code=401,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    user_id = auth.decode_token(credentials.credentials)
    if user_id is None:
        raise unauthorized
    user = repo.get_user(user_id)
    if user is None:
        raise unauthorized
    return user


def get_owned_project(
    project_id: str,
    user: UserRecord = Depends(get_current_user),
    repo: Repository = Depends(get_repo),
) -> ProjectRecord:
    """Row-level authorization (PRD §7): non-owners get 404, not 403."""
    project = repo.get_project(project_id)
    if project is None or project.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project
