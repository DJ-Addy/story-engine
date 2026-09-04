"""FastAPI dependencies: repository access and authentication."""

from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app import config  # noqa: F401  # loads backend/.env before env reads
from app.adapters.base import (
    LLMProvider,
    TerminalProviderError,
    TTSProvider,
    VideoProvider,
)
from app.api import auth
from app.api.repo import InMemoryRepository, ProjectRecord, Repository, UserRecord

# Module-level singleton so all requests share state; tests override get_repo
# with a fresh InMemoryRepository via app.dependency_overrides.
_repo: Repository = InMemoryRepository()

_bearer = HTTPBearer(auto_error=False)


def get_repo() -> Repository:
    return _repo


def get_tts() -> TTSProvider:
    """Default TTS provider: Google Cloud Text-to-Speech (Gemini-TTS voices).

    Selection is credential-based, not provider-based: a configured Google Cloud
    project picks the adapter, and there is no keyless fallback, so this raises
    when the project is unset. ``GOOGLE_APPLICATION_CREDENTIALS`` (or any other
    Application Default Credentials source — gcloud login, or the metadata server
    on Cloud Run/GCE) supplies the token; ``GOOGLE_CLOUD_PROJECT`` names the
    billing/quota project.

    ``app.config`` (imported at module load) has already merged backend/.env into
    the environment. The adapter is imported lazily so offline test collection
    never pulls in the aiohttp/google-auth stack; tests always override this with
    app.adapters.fake.FakeTTS via app.dependency_overrides.
    """
    import os

    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        raise TerminalProviderError(
            "no TTS provider configured (set GOOGLE_CLOUD_PROJECT and "
            "GOOGLE_APPLICATION_CREDENTIALS)"
        )

    from app.adapters.google_tts import GoogleTTSAdapter

    return GoogleTTSAdapter()


def get_video() -> VideoProvider:
    """Default video renderer: Veo on Vertex AI.

    Same credential-based selection as ``get_tts``; Veo is region-pinned, so
    ``GOOGLE_CLOUD_LOCATION`` (default ``us-central1``) also applies. Raises when
    no Google Cloud project is configured.

    The adapter is imported lazily so offline test collection never pulls in the
    aiohttp/google-auth stack.
    """
    import os

    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        raise TerminalProviderError(
            "no video provider configured (set GOOGLE_CLOUD_PROJECT and "
            "GOOGLE_APPLICATION_CREDENTIALS)"
        )

    from app.adapters.veo import VeoAdapter

    return VeoAdapter()


def get_llm() -> LLMProvider:
    """Default LLM: Gemini on Vertex AI.

    Powers the optional-LLM paths (novel conversion, shot-list generation, the
    rubric judges), which fall back to deterministic heuristics when no provider
    is injected. Same credential-based selection and lazy import as the others.
    """
    import os

    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        raise TerminalProviderError(
            "no LLM provider configured (set GOOGLE_CLOUD_PROJECT and "
            "GOOGLE_APPLICATION_CREDENTIALS)"
        )

    from app.adapters.gemini import GeminiAdapter

    return GeminiAdapter()


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
