"""Load the local ``backend/.env`` once, before any environment-variable reads.

Importing this module (see ``app.api.deps``, which imports it at the top) calls
``load_dotenv`` on ``backend/.env`` so provider credentials placed there become
visible to ``os.environ``. Real process environment variables take precedence
(``load_dotenv`` defaults to ``override=False``), and the call silently no-ops
when ``.env`` is absent, so importing this module is always safe.
"""

from pathlib import Path

from dotenv import load_dotenv

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"  # backend/.env
load_dotenv(_ENV_PATH)  # real environment variables take precedence (override=False default)
