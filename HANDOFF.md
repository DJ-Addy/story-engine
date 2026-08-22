# Story Engine — Build Handoff

**Date:** 2026-08-22 (updated end of session)
**Session summary:** Greenfield build. Eleven parallel agents built the backend core,
API layer, and first frontend workspace, all test-driven. **367 backend tests pass**
(verified by pytest) and the frontend **builds clean** (`tsc --noEmit` + `next build`).
Three commits on `main`: `91db95e` (core), `d9220e8` (API/workers/generation), `48cb962` (frontend).

**Source docs:** `C:\Users\Adam\Downloads\PRD.md` and `C:\Users\Adam\Downloads\HANDOFF.md`
(product handoff). Consider copying both into the repo.

**Environment note:** the Cursor sandbox cannot enforce filesystem isolation on this
machine, so shell commands only run with full ("all") permissions. If a future session
sees commands hang with no output, that is the cause — request full permissions.

---

## 1. How to run everything

```powershell
# Backend tests (venv already exists at backend/.venv)
cd "C:\Users\Adam\Desktop\Audiobooks from llms\backend"
.\.venv\Scripts\python.exe -m pytest -q          # expect: 367 passed

# API server (in-memory repo — no DB needed yet)
.\.venv\Scripts\python.exe -m uvicorn app.api.main:create_app --factory --reload

# Frontend (Next.js 16.3.2, App Router)
cd ..\frontend
npm run dev                                       # then open /scenes/demo
```

## 2. Where we are in the PRD (milestones §8)

| Milestone | Status |
|---|---|
| M0 Validation (human) | OPEN — founder must run 3 scripts past 5 directors + 5 authors (product HANDOFF §11). Engineering proceeded by explicit user request. |
| M1 Skeleton | **DONE at the app layer.** Auth (JWT + PBKDF2), projects with rights attestation, script upload → Fountain/FDX parse → story graph, manual line correction. Runs on an in-memory repository; PostgreSQL persistence is wired in models/migrations but not yet connected to the API. |
| M2 Audio | Logic complete + job orchestration (speech-bus timing, mix graphs, fan-out planning, cost estimation). Missing: real TTS adapters (ElevenLabs/Kokoro), actual worker execution against Redis, audio timeline UI. |
| M3 Shot list | **Largely done.** Schema, generation orchestration with retry-feedback, coverage-gap regen, all 6 continuity rules, grammar profiles, API endpoints, and the UI (editor + panel + axis diagram, mock data). Missing: real LLM adapter, API↔UI wiring. |
| M4 Visual | Prompt assembly + variant selection done. Missing: image provider adapters, generation workers, board grid UI. |
| M5 Animatic | Assembly commands + manifest builder done. Missing: execution + player UI. |
| M6 Hardening | Cost governor, retries, idempotency, partial-failure reconciliation, SSE contract all built and tested. Missing: OTel/Sentry, rate limiting. |

## 3. Repository map

```
backend/  (Python 3.12, FastAPI, Pydantic v2 — 367 tests, all green)
  app/ingest/      fountain.py, fdx.py, pdf_screenplay.py (modal-margin indent classifier),
                   normalize.py (cue attribution conf 1.0), elements.py (shared IR contract)
  app/nlp/         ambience.py (rule-based scene -> ambience tags)
  app/continuity/  6 rules, registry, grammar profiles, validator (pure/deterministic)
  app/shotlist/    schema.py, coverage.py (gap-only regen), repair.py, generate.py
                   (LLM prompt contract + retry-with-error-feedback)
  app/render/      audio/ (gap-table timing, sidechain mix argv, duration heuristics),
                   visual/ (board + charsheet prompts, variant_for), animatic.py
  app/adapters/    protocols, error taxonomy, deterministic fakes, registry
                   (LAW: no provider SDK outside this package)
  app/costs/       governor (cap guard, ledger), retry + idempotency_key
  app/workers/     plan.py (fan-out/fan-in JobSpec), events.py (SSE contract), tasks.py (arq)
  app/api/         FastAPI app factory, JWT auth, repo protocol + InMemoryRepository,
                   routers (auth/projects/scripts/scenes), /api/v1 prefix
  app/db/          SQLAlchemy models (13 tables, gist exclusion constraint) + Alembic 0001
frontend/  (Next.js 16, TS, Tailwind v4, zustand — builds clean)
  lib/             types.ts (backend contract), continuity.ts (client mirror of
                   AXIS_CROSS + LENS_JUMP for optimistic feedback), store.ts, api.ts
                   (MockApi behind StoryEngineApi interface), mock.ts
  components/      ShotListEditor (dense table, arrows/Enter/Tab/Escape), ContinuityPanel
                   (grouped findings, mark-deliberate, strict/silent), AxisDiagram (SVG)
  app/scenes/demo  the scene workspace
```

## 4. Architectural invariants (do not violate)

- No provider SDK imported outside `app/adapters/`. Tests use fakes; never spend credits in tests.
- Cost governor `guard()` before any enqueue; adapters report `cost_cents`; caps also set provider-side.
- Terminal errors (400/403/422/policy) never retried; 429/5xx retried with backoff.
- Parsers emit flat `RawElement` lists; only `normalize.py` builds scenes/characters.
- Screenplay attribution from cues = confidence 1.0, source `"cue"`.
- Continuity warns, never blocks (mark-as-deliberate); `validator_mode` filters severity (strict=all, lenient=no info, off=skip).
- Character variants cannot overlap (DB gist constraint; `variant_for` mirrors it in memory).
- Frontend data access only through `lib/api.ts`; UI state in zustand; optimistic findings reconcile against server findings by (rule, ordinal).

## 5. Next steps, in order

1. **Wire Postgres:** implement `SqlAlchemyRepository` against the existing models + `alembic upgrade head`; keep `InMemoryRepository` for tests. Needs a local Postgres 16 (`docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16`).
2. **First real adapters:** one TTS (ElevenLabs or self-hosted Kokoro) and one LLM (Anthropic) implementing the existing protocols; env keys via `.env` (see `.env.example`); set provider-side spend caps first (product HANDOFF risk #5).
3. **Worker execution:** Redis + arq runner for the planned jobs; SSE endpoint streaming `app/workers/events.py` payloads; then an end-to-end scene audio render.
4. **Frontend wiring:** replace `MockApi` with a fetch client against `/api/v1`; add upload + project pages; then the audio timeline (wavesurfer.js) per PRD §6.2 #4.
5. **PDF ingest end-to-end test** with a real screenplay PDF fixture through `extract_word_boxes`.
6. **M0 validation** remains a human task and gates go-to-market, not engineering.

## 6. Known issues / notes

- `app/api` uses an in-memory repo; data does not survive restarts yet (by design until step 1).
- Loop-extension in `mix.py` uses `-stream_loop` + ceil math; equal-power crossfade looping is a noted refinement.
- Dev JWT secret defaults to `dev-secret-change-me`; set `STORY_ENGINE_SECRET` in any deployed environment.
- `create-next-app` produced Next.js 16.3.2 (PRD says 15 — newer, no issues).
- Node 20.12.1 triggers benign `EBADENGINE` warnings from eslint deps; upgrading Node to ≥20.19 silences them.
