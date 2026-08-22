"""Story Engine API application factory."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from app.api.routers import auth, novel, projects, scenes, scripts


def create_app() -> FastAPI:
    app = FastAPI(title="Story Engine API", version="0.1.0")

    api = APIRouter(prefix="/api/v1")

    @api.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    api.include_router(auth.router)
    api.include_router(projects.router)
    api.include_router(scripts.router)
    api.include_router(novel.router)
    api.include_router(scenes.router)

    app.include_router(api)
    return app


app = create_app()
