"""Seed (once) the demo project, and answer whether it is there.

Three constraints shape this module:

* **The content has to be real.** The graph is produced by the same
  ``parse_fountain`` -> ``normalize`` pipeline that ``POST /script`` runs, over a
  screenplay file, and persisted through the ordinary repository calls. A
  hand-written ``StoryGraph`` literal would drift from the parser the moment
  either changed, and would quietly misrepresent what the ingest tier does.
* **It must not weaken auth.** The demo is a real, separate user with a real
  password hash — it just holds a random password nobody is ever told, so
  ``POST /demo/session`` is the only door into it. Every other user's login path
  is untouched, and the demo user owns exactly one project, so handing out its
  token exposes nothing but the sample.
* **It must be safe to call repeatedly.** Startup calls it, and so does every
  ``POST /demo/session``; two of those can arrive at once on a cold instance.
  Existence is therefore decided from the *repository*, under a lock, rather
  than from a process-local "already done" flag — a flag would be wrong the
  moment the repository it was set for is not the repository being asked about
  (exactly what happens under the test suite's dependency overrides).
"""

from __future__ import annotations

import os
import secrets
import threading
from dataclasses import dataclass
from importlib import resources

from app import config  # noqa: F401  # loads backend/.env before env reads
from app.api import auth
from app.api.repo import ProjectRecord, Repository, UserRecord
from app.ingest.fountain import parse_fountain
from app.ingest.normalize import normalize

# The demo user's address. A .dev domain nobody can receive mail at, so this
# can never collide with a real signup.
DEMO_EMAIL = "demo@story-engine.dev"

# Title of the seeded project, matching the screenplay's own title page. It is
# also how the project is *found* again among anything the demo token may have
# created since, so it doubles as the seed's identity — do not change it
# casually.
DEMO_TITLE = "The Lighthouse Wager"

_SCREENPLAY = "lighthouse.fountain"

# Values that turn the demo off. Anything else (including unset and empty) leaves
# it on: a deployment that wants a demo should get one without configuring
# anything, and one that does not is making a deliberate choice.
_DISABLED = frozenset({"0", "false", "no", "off"})

# Held across the read-then-write of the seed. Sync route handlers run in
# FastAPI's threadpool, so concurrent first requests are genuinely concurrent
# threads, and "does the demo exist yet?" followed by "create it" is not atomic
# in any repository we have.
_SEED_LOCK = threading.Lock()


@dataclass(frozen=True)
class DemoProject:
    """What the demo endpoints need to describe or hand out the demo."""

    user: UserRecord
    project: ProjectRecord
    scene_count: int


def demo_enabled() -> bool:
    """Whether this deployment serves the demo project.

    Default-on, unlike the analytics and storage switches, because those need
    credentials to do anything and this needs nothing: the useful default for a
    reviewer opening a fresh deployment is that something is already there.
    """
    return os.environ.get("STORY_ENGINE_DEMO", "").strip().lower() not in _DISABLED


def screenplay_text() -> str:
    """The packaged demo screenplay.

    Read through ``importlib.resources`` rather than a path relative to
    ``__file__`` so it resolves the same whether the package is installed into a
    venv (what the container does) or imported from the source tree.
    """
    return resources.files(__package__).joinpath(_SCREENPLAY).read_text(encoding="utf-8")


def _demo_project(repo: Repository, owner_id: str) -> ProjectRecord | None:
    """The seeded project among this user's projects, by title.

    ``list_projects(...)[0]`` would be wrong: a visitor holding a demo session
    token can create projects of their own, and the seed must keep pointing at
    the one it made.
    """
    return next(
        (p for p in repo.list_projects(owner_id) if p.title == DEMO_TITLE), None
    )


def find_demo(repo: Repository) -> DemoProject | None:
    """Report the demo project if it has been seeded. Never creates anything."""
    user = repo.get_user_by_email(DEMO_EMAIL)
    if user is None:
        return None
    project = _demo_project(repo, user.id)
    if project is None:
        return None
    script = repo.get_script(project.id)
    return DemoProject(
        user=user,
        project=project,
        scene_count=len(script.graph.scenes) if script is not None else 0,
    )


def _create_demo_user(repo: Repository) -> UserRecord:
    """A real user row, with a real hash of a password that is never shown.

    Generated fresh each time rather than taken from configuration, because a
    configured demo password is a credential someone eventually reuses. Nothing
    keeps it: it is hashed and dropped on the next line.
    """
    salt = auth.new_salt()
    return repo.create_user(
        DEMO_EMAIL, auth.hash_password(secrets.token_urlsafe(32), salt), salt
    )


def ensure_seeded(repo: Repository) -> DemoProject:
    """Create the demo user, project and story graph if they are not there yet.

    Each of the three pieces is created only when missing, so a run that was
    interrupted half-way (or a repository that lost one of them) completes
    rather than duplicating: this is resumable, not just repeatable.
    """
    with _SEED_LOCK:
        user = repo.get_user_by_email(DEMO_EMAIL) or _create_demo_user(repo)
        project = _demo_project(repo, user.id)
        if project is None:
            project = repo.create_project(
                owner_id=user.id,
                title=DEMO_TITLE,
                grammar_profile="classical",
                validator_mode="strict",
                # The screenplay is this project's own, written for it, so the
                # attestation is a statement of fact — and without it the render
                # and timeline-edit endpoints refuse, which would leave a
                # reviewer looking at a project they cannot do anything with.
                rights_attested=True,
            )
        script = repo.get_script(project.id)
        if script is None:
            script = repo.save_script(
                project.id, "fountain", normalize(parse_fountain(screenplay_text()))
            )
        return DemoProject(
            user=user, project=project, scene_count=len(script.graph.scenes)
        )
