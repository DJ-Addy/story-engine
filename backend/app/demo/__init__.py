"""The demo project a fresh deployment serves before anyone has uploaded anything.

A cold Cloud Run instance starts with an empty repository, and the UI has no
"create a project" flow — so without this the first visitor to the hosted URL
meets a 401 and an error state rather than the product. Seeding gives that
visitor a real story graph, built by the real ingest pipeline over a screenplay
that ships inside this package (``lighthouse.fountain``), not a hand-written
fixture pretending to be one.

The screenplay lives here rather than in ``backend/tests/fixtures`` because
``.dockerignore`` excludes ``backend/tests`` from the build context: anything the
container needs at runtime has to be inside the installed package.
"""

from app.demo.seed import (
    DEMO_EMAIL,
    DEMO_TITLE,
    DemoProject,
    demo_enabled,
    ensure_seeded,
    find_demo,
    screenplay_text,
)

__all__ = [
    "DEMO_EMAIL",
    "DEMO_TITLE",
    "DemoProject",
    "demo_enabled",
    "ensure_seeded",
    "find_demo",
    "screenplay_text",
]
