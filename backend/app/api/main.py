"""Story Engine API application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse

from app.adapters.base import (
    ProviderError,
    RetryableProviderError,
    TerminalProviderError,
)
from app.analytics.recorder import (
    EventRecorder,
    build_recorder,
    set_recorder,
    shutdown_recorder,
)
from app.analytics.settings import AnalyticsSettings
from app.api.deps import get_repo
from app.api.routers import (
    agent,
    analytics,
    assist,
    auth,
    boards,
    demo,
    judge,
    novel,
    projects,
    renders,
    scenes,
    scripts,
)
from app.demo import seed as demo_seed

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own the ClickHouse event recorder, and make the instance worth visiting.

    The recorder is built at startup rather than lazily on first use so the
    buffer and its drain task belong to the serving event loop, and torn down at
    shutdown so the last events are flushed and the MCP session is closed once,
    not per request.

    Building is cheap and offline: ``build_recorder`` reads settings and
    constructs a runner, but no MCP server is launched and no socket is opened
    until the first statement. So an absent ``CLICKHOUSE_HOST`` (the offline/dev
    default) yields a disabled recorder whose ``record()`` is a no-op, and even a
    misconfigured one disables analytics rather than stopping the API booting.
    """
    try:
        recorder = build_recorder(AnalyticsSettings.from_env())
    except Exception as exc:  # a bad analytics config must never block startup
        logger.warning("analytics disabled: recorder could not be built: %s", exc)
        recorder = EventRecorder(None)
    set_recorder(recorder)

    # Seed the demo project before the instance takes traffic. POST
    # /demo/session would seed it anyway, but a cold Cloud Run instance is
    # exactly when the first visitor arrives, and paying for the parse there
    # makes their first click slower for no reason. Wrapped like the recorder
    # above and for the same reason: a demo that cannot be built is a missing
    # sample, not grounds for refusing to serve the API at all.
    if demo_seed.demo_enabled():
        try:
            demo_seed.ensure_seeded(get_repo())
        except Exception as exc:  # a missing demo must never block startup
            logger.warning("demo project not seeded at startup: %s", exc)

    try:
        yield
    finally:
        await shutdown_recorder()


def create_app() -> FastAPI:
    app = FastAPI(title="Story Engine API", version="0.1.0", lifespan=lifespan)

    # A provider that cannot serve the request is a deployment fact, not a crash.
    # ``agent.py`` already answers this condition with a 503 whose detail names
    # the environment variables to set; the render endpoints raised the same
    # exceptions out of their dependencies with nobody catching them, so
    # "no video provider configured (set GOOGLE_CLOUD_PROJECT and
    # GOOGLE_APPLICATION_CREDENTIALS)" reached the client as an opaque
    # ``500 Internal Server Error`` with a plain-text body and a stack trace in
    # the logs. Handling it here gives every route the agent router's contract:
    # a JSON ``detail`` a caller can act on.
    #
    # 503 for terminal (this deployment cannot do it — configuration, policy),
    # 502 for retryable (the upstream provider itself failed). Routes that
    # already catch these locally are unaffected: a handled exception never
    # reaches an application handler.

    @app.exception_handler(TerminalProviderError)
    async def _terminal_provider_error(
        _request: Request, exc: TerminalProviderError
    ) -> JSONResponse:
        logger.warning("provider unavailable: %s", exc)
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(RetryableProviderError)
    async def _retryable_provider_error(
        _request: Request, exc: RetryableProviderError
    ) -> JSONResponse:
        logger.warning("provider failed upstream: %s", exc)
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(ProviderError)
    async def _provider_error(_request: Request, exc: ProviderError) -> JSONResponse:
        # Any future subclass still answers with a readable body, not a 500.
        logger.warning("provider error: %s", exc)
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    api = APIRouter(prefix="/api/v1")

    @api.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    api.include_router(auth.router)
    # Unauthenticated by design; see app/api/routers/demo.py for why that is
    # not a hole in the auth model.
    api.include_router(demo.router)
    api.include_router(projects.router)
    api.include_router(scripts.router)
    api.include_router(novel.router)
    api.include_router(scenes.router)
    # Same prefix as the scene router: the assistant is the scene's editing
    # surface, and it keeps its own module because it is the only route here
    # that spends provider credits.
    api.include_router(assist.router)
    api.include_router(renders.router)
    # Boards are the still half of the render pipeline: a frame stored here
    # is what flips renders.router's Veo call to image-to-video.
    api.include_router(boards.router)
    api.include_router(judge.router)
    api.include_router(analytics.router)
    # Two routers, one module: the runs are project-scoped, the network
    # description is deployment-scoped (like /health) and takes no project.
    api.include_router(agent.router)
    api.include_router(agent.meta_router)

    app.include_router(api)
    return app


app = create_app()
