# Story Engine — Build Handoff

**Date:** 2026-08-22
**Session summary:** Greenfield build session. The workspace started empty; six parallel
agents built the core backend modules test-first. Everything below is authored but
**unverified by pytest** — the shell in this Cursor session was broken (commands returned
no output, for the main agent and all subagents), so no installs or test runs could execute.

**Source docs:** `C:\Users\Adam\Downloads\PRD.md` and `C:\Users\Adam\Downloads\HANDOFF.md`
(the product handoff). Consider copying both into this repo.

---

## 1. FIRST ACTIONS NEXT SESSION (do these before anything else)

Restart Cursor (or the machine) so the shell works again, then:

```powershell
cd "C:\Users\Adam\Desktop\Audiobooks from llms\backend"
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m pytest -v
```

Expected: ~115 tests across 13 test files. All code was hand-traced by its authoring
agent but never machine-run, so expect a handful of small failures (typos, Pydantic v2
API details). Fix failures before writing any new code — that completes the red/green
loop the TDD process started.

Then `git init` and make the first commit (git was never run; there is no repo yet).

---

## 2. Where we are in the PRD

PRD milestones (§8):

| Milestone | Status |
|---|---|
| M0 Validation (manual, human) | NOT DONE — requires the founder to run 3 scripts past 5 directors + 5 authors (product HANDOFF §11). Engineering proceeded in parallel by explicit user request. |
| M1 Skeleton (auth, projects, upload, Fountain+FDX parse, story graph persisted) | **~60% — this session's work.** Parsing, normalization, story graph model, and full DB schema are built. Missing: FastAPI app itself, auth, upload flow, wiring parse→DB. |
| M2 Audio | Logic layer built (timing, mix graphs, duration heuristics). Missing: real TTS adapters, workers, timeline UI. |
| M3 Shot list | Contract + validator built (schema, coverage, repair, all 6 continuity rules, grammar profiles). Missing: LLM prompt assembly + generation orchestration, editor UI, axis diagram. |
| M4 Visual / M5 Animatic / M6 Hardening | Not started (adapters + cost governor foundations exist for M6). |

## 3. What exists (all under `backend/`, Python 3.12, Pydantic v2)

| Module | Files | Tests |
|---|---|---|
| Ingest | `app/ingest/elements.py` (shared IR contract), `fountain.py`, `fdx.py`, `normalize.py` | `test_fountain.py`, `test_fdx.py`, `test_normalize.py` + fixtures `tests/fixtures/sample.fountain`, `sample.fdx` |
| Continuity | `app/continuity/model.py`, `registry.py`, `rules.py` (AXIS_CROSS, EYELINE_MISMATCH, NO_REVERSE, LENS_JUMP, SCREEN_DIRECTION_FLIP, TIME_OF_DAY_DRIFT), `profiles.py`, `validator.py` | `test_continuity.py` (23 tests) |
| Shot list | `app/shotlist/schema.py` (ShotSpec/SceneShotList), `coverage.py` (gap-targeted regen), `repair.py` (never-raising LLM JSON parse) | 3 test files (27 tests) |
| Audio | `app/render/audio/model.py`, `timing.py` (PRD gap table, largest-gap rule), `mix.py` (sidechain duck ffmpeg argv, 3 presets), `durations.py` (pre-audio shot duration heuristic) | 3 test files (30 tests) |
| Adapters | `app/adapters/base.py` (protocols, error taxonomy, `classify_http_status`), `fake.py` (deterministic + failure injection), `registry.py` | `test_adapters.py` |
| Costs | `app/costs/governor.py` (guard + CostLedger), `retry.py` (`run_with_retries`, `idempotency_key`) | `test_cost_governor.py`, `test_retry.py` |
| DB | `app/db/base.py`, `models.py` (13 tables, native pg enums, **GiST exclusion constraint on character_variants** — the PRD's key integrity guarantee), `alembic/versions/0001_initial_schema.py` (hand-written, creates btree_gist) | `test_db_models.py` (metadata-only, no live DB needed) |

Repo root: `README.md`, `.gitignore`, `.env.example`, `backend/pyproject.toml`.

## 4. Architectural invariants already encoded (do not violate)

- No provider SDK imported outside `app/adapters/`. Tests use fakes only — never spend credits in tests.
- Cost governor `guard()` runs before any job enqueues; adapters report `cost_cents` on every result.
- Terminal provider errors (400/403/422/content-policy) are NEVER retried; 429/5xx are.
- Parsers emit flat `RawElement` lists; only `normalize.py` builds scenes/characters.
- Screenplay dialogue attribution comes from character cues at confidence 1.0, source `"cue"`.
- Continuity findings warn, never block ("Mark as deliberate" model).
- Character variants cannot overlap in scene range (DB-level exclusion constraint).

## 5. Next build steps, in order

1. **Fix any test failures** from the first pytest run (see §1).
2. **Finish M1:** FastAPI app factory + routes from PRD §5.4 (`POST /projects`, script upload with `rights_attested` enforcement, `GET /projects/{id}/graph`, `PATCH /lines/{id}`); session/JWT auth; wire `parse_fountain`/`parse_fdx` → `normalize` → SQLAlchemy persistence. Needs Postgres 16 (docker: `docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres postgres:16`), then `alembic upgrade head`.
3. **PDF screenplay parser** (`app/ingest/pdf_screenplay.py`): pdfplumber + indent-band classifier per PRD §4.2, with modal-x0 margin calibration. Add `pdfplumber` to pyproject.
4. **M2 audio pipeline:** arq worker tasks (fan-out tts_line × N → ambience → mix barrier per PRD §5.2), ElevenLabs + Kokoro adapters behind the existing protocols, SSE progress endpoint.
5. **M3 generation:** prompt assembly for shot lists (scene text + character list + grammar profile + previous scene's last shot), max-2-retry orchestration around the existing `parse_llm_shotlist`.
6. **Frontend:** Next.js 15 scaffold (`npx create-next-app@latest frontend`), then the five core components in PRD §6.2 — start with Shot List Editor + Continuity Panel.

## 6. Known issues / decisions made this session

- The whole suite is unverified (broken shell). Treat the first pytest run as the real TDD "red" phase.
- `tests/conftest.py` has a `sys.path` guard inserting `backend/` so `import app` works even without `pip install -e .`.
- `mix.py` loop-extension uses `-stream_loop` with ceil math; equal-power crossfade looping (PRD §4.5) is a noted TODO refinement.
- No git history exists — this session could not run git.
- Frontend not started (couldn't run npm; nothing hand-written to avoid an unbuildable half-scaffold).
- M0 validation gate (product HANDOFF §11) remains open — engineering was explicitly requested to proceed anyway.
