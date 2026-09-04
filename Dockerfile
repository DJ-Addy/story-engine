# syntax=docker/dockerfile:1.7
#
# Single-container image for Cloud Run: Next.js serves $PORT and proxies
# /api/v1 to uvicorn on loopback. Cloud Run exposes exactly one port, and
# frontend/next.config.ts already rewrites /api/v1 to API_PROXY_TARGET, so
# the two processes need no code change to live together — the proxy target
# simply becomes localhost.

# --------------------------------------------------------------------------
# Frontend build
# --------------------------------------------------------------------------
FROM node:22-bookworm-slim AS frontend-build
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /src/frontend

# package.json first so npm ci is cached until dependencies actually change.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
# Emits .next/standalone: a self-contained server.js plus only the modules it
# imports, so the runtime image needs no node_modules and no npm.
RUN npm run build

# --------------------------------------------------------------------------
# Backend dependencies
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS backend-build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
# psycopg[binary] and numpy ship wheels, but mcp-clickhouse's tree does not
# always, so a compiler has to be available here. It stays out of the runtime.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src/backend
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install .

# --------------------------------------------------------------------------
# Runtime
# --------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    PATH="/opt/venv/bin:${PATH}"

# libsndfile backs soundfile, used by the DSP path and the TTS adapter.
# ffmpeg is deliberately absent: app/render/audio/mix.py and render/animatic.py
# only assemble ffmpeg argv as data and never execute it (no subprocess call
# exists in app/), so shipping it would be ~100MB of cold-start for nothing.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libsndfile1 \
 && rm -rf /var/lib/apt/lists/*

# Next's standalone bundle runs on a bare node binary; no npm, no global
# modules. Copying just the binary keeps this image Python-shaped.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node

COPY --from=backend-build /opt/venv /opt/venv

WORKDIR /app
# The app package is installed into the venv; alembic still needs its scripts
# and ini on disk, and must run with this as the working directory because
# alembic.ini sets script_location to a relative path.
COPY backend/alembic ./backend/alembic
COPY backend/alembic.ini ./backend/alembic.ini

COPY --from=frontend-build /src/frontend/.next/standalone ./frontend/
COPY --from=frontend-build /src/frontend/.next/static ./frontend/.next/static
COPY --from=frontend-build /src/frontend/public ./frontend/public

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN useradd --create-home --uid 1001 appuser && chown -R appuser:appuser /app
USER appuser

# Cloud Run overrides this; the default keeps `docker run -p 8080:8080` working.
ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
