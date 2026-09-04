"""Environment-driven configuration for the ClickHouse analytics spine.

Two distinct groups of variables live here and they are deliberately not
merged:

* ``CLICKHOUSE_*`` — read verbatim by the official ``mcp-clickhouse`` server.
  Story Engine never interprets them beyond deciding whether analytics is
  configured at all; they are forwarded into the MCP server's process
  environment exactly as ClickHouse documents them, so a credential that works
  with ``mcp-clickhouse`` on the command line works here unchanged.
* ``STORY_ENGINE_*`` — how *this* service drives that server (transport,
  launch command, tool name, batching), i.e. the parts ClickHouse has no
  opinion about.

Absent ``CLICKHOUSE_HOST`` the whole subsystem reports ``enabled is False`` and
every call site degrades to a no-op. That is the offline/dev default: the API
must serve identically whether or not a cluster exists.
"""

from __future__ import annotations

import os
import shlex
import sys
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Transport = Literal["stdio", "http"]

# The MCP tool that mcp-clickhouse exposes for SQL. Current releases name it
# ``run_query`` (read-only unless CLICKHOUSE_ALLOW_WRITE_ACCESS=true); older
# releases shipped ``run_select_query``. Overridable so a pinned older server
# still works without a code change.
DEFAULT_TOOL = "run_query"

# Default database for Story Engine's event tables. Kept out of ClickHouse's
# own ``default`` so the migration never touches a shared namespace.
DEFAULT_DATABASE = "story_engine"

# ClickHouse Cloud's HTTPS interface. mcp-clickhouse picks 8443 itself when
# CLICKHOUSE_SECURE is true; we pass it explicitly so the value the operator
# sees in the status endpoint is the value actually used.
DEFAULT_PORT = "8443"


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


class AnalyticsSettings(BaseModel):
    """Resolved analytics configuration; construct with :meth:`from_env`."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    database: str = DEFAULT_DATABASE

    # --- how we reach the MCP server -------------------------------------
    transport: Transport = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    url: str | None = None
    auth_token: str | None = None
    tool_name: str = DEFAULT_TOOL
    timeout_s: float = 30.0

    # --- ClickHouse credentials handed to the MCP server ------------------
    # Mirrors mcp-clickhouse's own variable names 1:1 so the child process sees
    # exactly what ClickHouse's docs describe.
    server_env: dict[str, str] = Field(default_factory=dict)

    # --- write buffering --------------------------------------------------
    flush_interval_s: float = 2.0
    batch_size: int = 200
    buffer_max: int = 5000

    @property
    def host(self) -> str:
        return self.server_env.get("CLICKHOUSE_HOST", "")

    def describe(self) -> dict[str, object]:
        """Operator-facing summary. Never includes the password."""
        return {
            "enabled": self.enabled,
            "database": self.database,
            "transport": self.transport,
            "host": self.host,
            "port": self.server_env.get("CLICKHOUSE_PORT", ""),
            "user": self.server_env.get("CLICKHOUSE_USER", ""),
            "secure": self.server_env.get("CLICKHOUSE_SECURE", ""),
            "tool_name": self.tool_name,
            "command": " ".join([self.command, *self.args]).strip(),
            "url": self.url,
        }

    @classmethod
    def from_env(cls) -> "AnalyticsSettings":
        """Build settings from the process environment.

        ``CLICKHOUSE_HOST`` is the on/off switch: without a host there is
        nothing to connect to, so analytics is disabled rather than
        half-configured. ``STORY_ENGINE_ANALYTICS_ENABLED=0`` force-disables
        even when a host is present (useful to silence a flaky cluster mid-demo
        without unsetting credentials).
        """
        host = os.environ.get("CLICKHOUSE_HOST", "").strip()
        enabled = bool(host) and _env_flag("STORY_ENGINE_ANALYTICS_ENABLED", True)

        database = (
            os.environ.get("STORY_ENGINE_CLICKHOUSE_DATABASE")
            or os.environ.get("CLICKHOUSE_DATABASE")
            or DEFAULT_DATABASE
        ).strip()

        transport_raw = os.environ.get("STORY_ENGINE_MCP_TRANSPORT", "stdio").strip().lower()
        transport: Transport = "http" if transport_raw in {"http", "streamable-http"} else "stdio"

        command = os.environ.get("STORY_ENGINE_MCP_COMMAND", sys.executable)
        args_raw = os.environ.get("STORY_ENGINE_MCP_ARGS")
        # ``python -m mcp_clickhouse.main`` runs the official server from this
        # venv. ClickHouse's own docs instead show
        # ``uv run --with mcp-clickhouse --python 3.10 mcp-clickhouse``; set
        # STORY_ENGINE_MCP_COMMAND/ARGS to that if you prefer an isolated uv env.
        args = tuple(shlex.split(args_raw)) if args_raw else ("-m", "mcp_clickhouse.main")

        # mcp-clickhouse reads these itself. We only *forward* them, and we pin
        # the two that Story Engine's usage requires: the event tables have to
        # be created and inserted into, which the server refuses in its default
        # read-only mode.
        server_env: dict[str, str] = {
            "CLICKHOUSE_HOST": host,
            "CLICKHOUSE_PORT": os.environ.get("CLICKHOUSE_PORT", DEFAULT_PORT),
            "CLICKHOUSE_USER": os.environ.get("CLICKHOUSE_USER", "default"),
            "CLICKHOUSE_PASSWORD": os.environ.get("CLICKHOUSE_PASSWORD", ""),
            "CLICKHOUSE_SECURE": os.environ.get("CLICKHOUSE_SECURE", "true"),
            "CLICKHOUSE_VERIFY": os.environ.get("CLICKHOUSE_VERIFY", "true"),
            "CLICKHOUSE_DATABASE": database,
            "CLICKHOUSE_CONNECT_TIMEOUT": os.environ.get("CLICKHOUSE_CONNECT_TIMEOUT", "30"),
            "CLICKHOUSE_ALLOW_WRITE_ACCESS": "true",
            "CLICKHOUSE_MCP_SERVER_TRANSPORT": "stdio",
        }
        if os.environ.get("CLICKHOUSE_ROLE"):
            server_env["CLICKHOUSE_ROLE"] = os.environ["CLICKHOUSE_ROLE"]

        return cls(
            enabled=enabled,
            database=database,
            transport=transport,
            command=command,
            args=args,
            url=os.environ.get("STORY_ENGINE_MCP_URL") or None,
            auth_token=os.environ.get("STORY_ENGINE_MCP_AUTH_TOKEN") or None,
            tool_name=os.environ.get("STORY_ENGINE_MCP_TOOL", DEFAULT_TOOL),
            timeout_s=_env_float("STORY_ENGINE_MCP_TIMEOUT_S", 30.0),
            server_env=server_env,
            flush_interval_s=_env_float("STORY_ENGINE_ANALYTICS_FLUSH_INTERVAL_S", 2.0),
            batch_size=_env_int("STORY_ENGINE_ANALYTICS_BATCH_SIZE", 200),
            buffer_max=_env_int("STORY_ENGINE_ANALYTICS_BUFFER_MAX", 5000),
        )
