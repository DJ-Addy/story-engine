# Story Engine

A single ingestion engine that turns a screenplay (later: manuscript) into a structured
story representation (the Story Graph IR), then renders that representation into:

1. An immersive multi-voice audiobook with environmental sound design
2. A previsualization package: shot list, continuity report, character boards, animatic

See `PRD.md` and `HANDOFF.md` (project docs) for full context. **The IR is the product** —
audio and visual outputs are renderers over the same story graph.

## Repository layout

```
backend/            FastAPI + workers (Python 3.12)
  app/
    ingest/         Fountain / FDX / PDF screenplay parsers -> story graph
    nlp/            attribution confidence, ambience tagging (phase 2: BookNLP)
    shotlist/       LLM shot generation + Pydantic schema contract
    continuity/     deterministic continuity rules + grammar profiles
    render/audio/   TTS orchestration, speech-bus timing, ffmpeg mix assembly
    render/visual/  character sheets, boards (image-to-image)
    adapters/       provider adapters — NO provider SDK imported outside here
    db/             SQLAlchemy models mirroring the PRD §3.2 schema
    costs/          cost governor
  tests/            pytest suite (TDD: tests are written with/before each module)
frontend/           Next.js 15 App Router (scaffolded in a later milestone)
```

## Development

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest
```

Requires Python 3.12+. PostgreSQL 16 and Redis are needed only for the API/worker
integration layer; the core modules (parsers, validator, schema, timing, cost governor)
are pure and run under pytest with no services.

## Engineering rules

- **TDD**: every module lands with its tests. Pure logic is kept free of I/O so it is
  testable without network or database.
- **Provider adapters are mandatory**: no provider SDK import outside `app/adapters/`.
- **Cost caps**: the cost governor is enforced before any job enqueues. Fake providers
  are used in all tests — tests must never spend API credits.
