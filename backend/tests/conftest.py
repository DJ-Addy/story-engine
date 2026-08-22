import sys
from pathlib import Path

import pytest

# Ensure `app` is importable when the package isn't installed editable.
_BACKEND_ROOT = str(Path(__file__).parent.parent)
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def sample_fountain(fixtures_dir: Path) -> str:
    return (fixtures_dir / "sample.fountain").read_text(encoding="utf-8")


@pytest.fixture
def sample_fdx(fixtures_dir: Path) -> str:
    return (fixtures_dir / "sample.fdx").read_text(encoding="utf-8")
