"""The one unauthenticated door into the demo project.

Both routes are deliberately anonymous: their whole purpose is to serve someone
who has no account and no way to make one. What keeps that from being a hole in
the auth model is *what* the token they get is for — a user that owns nothing
but the sample screenplay, whose password is random and discarded. Every other
user's data stays behind the same bearer check it always was.

Written as sync handlers so they run in FastAPI's threadpool: the seeder holds a
``threading.Lock`` across repository calls, which must not be taken on the event
loop.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from app.api import auth
from app.api.deps import get_repo
from app.api.repo import Repository
from app.api.schemas import DemoSession, DemoStatus
from app.demo import seed

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("", response_model=DemoStatus)
def demo_status(repo: Repository = Depends(get_repo)) -> DemoStatus:
    """Is there a demo, and what is it?

    This is the first call a freshly loaded client makes, before it has a token
    or anything to show, so it answers rather than fails: a repository that
    cannot be read reports "not seeded yet" (and says so in the log) instead of
    turning the landing page into a 500. The seeded state is read, never
    created — creating is ``POST /demo/session``'s job, so a client polling this
    endpoint cannot cause writes.
    """
    if not seed.demo_enabled():
        return DemoStatus()
    try:
        found = seed.find_demo(repo)
    except Exception as exc:  # defensive: the landing page must still render
        logger.warning("demo status unavailable: %s", exc)
        return DemoStatus(enabled=True)
    if found is None:
        return DemoStatus(enabled=True)
    return DemoStatus(
        enabled=True,
        seeded=True,
        project_id=found.project.id,
        title=found.project.title,
        scene_count=found.scene_count,
    )


@router.post("/session", response_model=DemoSession)
def demo_session(repo: Repository = Depends(get_repo)) -> DemoSession:
    """Mint a token for the demo user, seeding the demo first if needed.

    Startup already seeds, so this normally finds the project sitting there; the
    seed call stays because a repository that outlives one container (Cloud SQL)
    may have been emptied since, and because the startup pass is allowed to fail
    silently. ``ensure_seeded`` is idempotent, so the repeat costs a lookup.

    The token comes from the same ``app.api.auth`` helper ``/auth/login`` uses —
    same secret, same 60-minute TTL — so nothing downstream can tell a demo
    session from a real one, and nothing had to be relaxed to let it in.
    """
    if not seed.demo_enabled():
        raise HTTPException(
            status_code=403,
            detail=(
                "The demo project is disabled on this deployment "
                "(STORY_ENGINE_DEMO is set to off)"
            ),
        )
    demo = seed.ensure_seeded(repo)
    return DemoSession(
        access_token=auth.create_token(demo.user.id), project_id=demo.project.id
    )
