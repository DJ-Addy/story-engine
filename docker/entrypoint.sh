#!/bin/bash
# Starts uvicorn on loopback and Next.js on $PORT, and makes either process
# dying take the container down so Cloud Run replaces it rather than serving a
# half-dead instance.
set -euo pipefail

: "${PORT:=8080}"
: "${BACKEND_PORT:=8000}"

# Same-origin proxying: Next forwards /api/v1 to the backend in this container.
# The FastAPI app mounts no CORS middleware, so this is the only supported path.
export API_PROXY_TARGET="http://127.0.0.1:${BACKEND_PORT}"

# Alembic is the supported schema path. Only run it when a database is actually
# configured — with none, the app uses the in-memory repository and migrating
# would be both pointless and a startup failure.
if [[ -n "${DATABASE_URL:-}" || -n "${INSTANCE_UNIX_SOCKET:-}" || -n "${CLOUD_SQL_CONNECTION_NAME:-}" ]]; then
  echo "[entrypoint] database configured; running alembic upgrade head"
  ( cd /app/backend && alembic upgrade head )
else
  echo "[entrypoint] no database configured; using the in-memory repository"
fi

echo "[entrypoint] starting uvicorn on 127.0.0.1:${BACKEND_PORT}"
uvicorn app.api.main:app --host 127.0.0.1 --port "${BACKEND_PORT}" &
backend_pid=$!

# Next's standalone server binds process.env.HOSTNAME, which defaults to
# localhost — unreachable from outside the container.
export HOSTNAME=0.0.0.0
echo "[entrypoint] starting next on 0.0.0.0:${PORT}"
node /app/frontend/server.js &
frontend_pid=$!

shutdown() {
  echo "[entrypoint] signal received; stopping children"
  kill -TERM "${backend_pid}" "${frontend_pid}" 2>/dev/null || true
  wait "${backend_pid}" "${frontend_pid}" 2>/dev/null || true
  exit 0
}
trap shutdown TERM INT

# Return as soon as EITHER child exits, so a dead backend behind a live
# frontend cannot keep passing health checks.
wait -n "${backend_pid}" "${frontend_pid}"
echo "[entrypoint] a child process exited; shutting down"
kill -TERM "${backend_pid}" "${frontend_pid}" 2>/dev/null || true
exit 1
