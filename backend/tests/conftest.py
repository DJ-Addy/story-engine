import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Ensure `app` is importable when the package isn't installed editable.
_BACKEND_ROOT = str(Path(__file__).parent.parent)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True, scope="session")
def analytics_offline() -> Iterator[None]:
    """No test may reach a ClickHouse cluster.

    ``create_app``'s lifespan builds the analytics recorder from the
    environment, and ``backend/.env`` is merged into it on import — so once an
    operator adds real ``CLICKHOUSE_*`` credentials for the demo, an unguarded
    suite would start launching the ``mcp-clickhouse`` child process and writing
    a test run's judge scores into the live cluster. Force-disabling here keeps
    that impossible whatever the machine is configured for; the analytics tests
    build their own recorder over an in-memory runner instead.
    """
    previous = os.environ.get("STORY_ENGINE_ANALYTICS_ENABLED")
    os.environ["STORY_ENGINE_ANALYTICS_ENABLED"] = "0"
    yield
    if previous is None:
        os.environ.pop("STORY_ENGINE_ANALYTICS_ENABLED", None)
    else:
        os.environ["STORY_ENGINE_ANALYTICS_ENABLED"] = previous


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def sample_fountain(fixtures_dir: Path) -> str:
    return (fixtures_dir / "sample.fountain").read_text(encoding="utf-8")


@pytest.fixture
def sample_fdx(fixtures_dir: Path) -> str:
    return (fixtures_dir / "sample.fdx").read_text(encoding="utf-8")
