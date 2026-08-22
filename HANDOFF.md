# Story Engine — Session Handoff

**Date:** 2026-08-22  
**Repo:** `C:\Users\Adam\Desktop\Audiobooks from llms`  
**Working name:** Story Engine  
**This document:** everything said in the current chat, the product intent those messages imply, what exists in the repo now, and what the next session should do first.

Source product docs (not yet copied into the repo):

- `C:\Users\Adam\Downloads\PRD.md`
- `C:\Users\Adam\Downloads\HANDOFF.md` (original product handoff)

**Environment note:** the Cursor sandbox cannot enforce filesystem isolation on this machine, so shell commands only run with full ("all") permissions. If a future session sees commands hang with no output, request full permissions.

---

## 1. Everything said in this chat (verbatim)

User messages, in order:

1. **continue to do as much you can**
2. **Continue and launch the frontend I want the a glass ui and use avaivable libraries for this i love the glass ui but this is just a test, ultimalitely i want it clean. I want the voices to have emotions and for the audio to be crisp.**
3. **launch demo**
4. **stop it**
5. **okay now i want you to focus on ui Look up the best tools for a animated website and generate a landpage with beatiful graphics. also focus on the audio generation and find a way to transcribe a book into a screen play if applicable**
6. **make a handoff doc in the project with everything said in this chat**

Timestamps on all of the above: Saturday, Aug 22, 2026, 5:37 PM (UTC-5). The chat is a continuation of the earlier greenfield build (see §4).

---

## 2. What the founder asked for (decoded)

These are the standing product preferences from this chat. Do not treat them as discarded just because later messages added more work.

### UI direction

- Glass UI was requested as a **test / experiment**, using available libraries. The founder likes glass, but **the real target is clean**.
- After the demo was launched and then stopped, the next UI ask was: look up the best tools for an **animated website** and generate a **landing page with beautiful graphics**.
- The scene workspace (`/scenes/demo`) is still the product UI; the landing page is the public face. Keep the workspace dense and clean, not glass-for-its-own-sake.

### Audio direction

- **Voices must have emotions.** The TTS contract already takes `emotion: str | None`. Job planning already passes `line["emotion"]`. The live scene renderer currently hard-codes `emotion=None`. Edge TTS records emotion but cannot apply it. This is unfinished.
- **Audio must be crisp.** The DSP mix aims for dialogue-forward loudness (about −18 LUFS) with ambience sidechain-ducked under speech. Crispness still depends on a real neural TTS, not the Edge fallback or placeholder tones.

### Book → screenplay

- Founder asked to **transcribe a book into a screenplay if applicable**. That is applicable: indie authors adapting their own novels are the primary customer in the product handoff.
- A rule-based novel → Fountain path now exists (`app.ingest.novel` + `app.ingest.fountain_writer`). Converted Fountain then re-enters the existing screenplay pipeline so novels and scripts share one IR.

### Process

- "Continue and do as much as you can" / "launch the frontend" / "launch demo" / "stop it" means: keep shipping, start the Next.js app when asked, and kill the demo server when asked. Do not leave stray `next dev` processes running.

---

## 3. What this session added on top of the first build

The first session (see §4) built the backend core, API layer, and scene workspace. Work after that includes:

### Animated landing page (clean, not glass)

- Route: `/` (`frontend/app/page.tsx`)
- Libraries chosen after the "best tools for an animated website" ask:
  - **Motion** (`motion`) — React-first animation, reduced-motion aware
  - **GSAP + ScrollTrigger + `@gsap/react`** — scroll-scrubbed parallax
  - **Lenis** — smooth scroll (`frontend/components/landing/SmoothScroll.tsx`)
  - **Next.js Image** + zinc/amber/sky palette
- Sections: `Hero`, `PipelineStory`, `FeatureGrid`, `NumbersStrip`, `FooterCta`
- Hero headline: "Every line. Every voice. Every frame."
- CTA goes to `/scenes/demo`
- **Broken asset:** `Hero.tsx` references `/hero-story-graph.png`, which is **not in `frontend/public/`**. The hero backdrop will 404 until that image is generated or the `Image` src is replaced.

### Audio generation

- First real TTS adapter: `backend/app/adapters/edge.py` — Microsoft neural voices via `edge-tts`, no API key. Six curated voices. MP3 decoded to 16-bit WAV when libsndfile supports MP3.
- Pure-numpy DSP engine: `backend/app/render/audio/dsp.py` — procedural ambience, speech-bus placement, sidechain ducking, loudness normalize. Sample rate 24 kHz. No ffmpeg, no scipy.
- End-to-end scene renderer: `backend/app/render/audio/pipeline.py` — TTS → speech-bus plan → ambience bed → mix → WAV bytes.
- Tests: `test_edge.py`, `test_dsp.py`, `test_pipeline_audio.py`. Network Edge tests are marked `@pytest.mark.network` and deselected by default (`addopts = -m 'not network'`).

### Book → screenplay

- `backend/app/ingest/novel.py` — chapter/scene split, quote extraction, tiered attribution, optional LLM repair for low-confidence quotes only, `novel_to_screenplay()`.
- `backend/app/ingest/fountain_writer.py` — emits Fountain so converted novels parse back through `parse_fountain` + `normalize` as first-class screenplays (cue attribution confidence 1.0). Unattributed quotes become `UNKNOWN SPEAKER`.
- Fixture: `backend/tests/fixtures/sample_novel.txt`
- Tests: `test_novel.py`, `test_fountain_writer.py`

### Demo ops

- Frontend was launched (`npm run dev`, `/scenes/demo`) and then stopped on request. Do not assume a server is still running.

---

## 4. Prior session (greenfield) — still true

Three commits on `main` from the first build: `91db95e` (core), `d9220e8` (API/workers/generation), `48cb962` (frontend). Backend was **367 tests green** at that point; later modules added more tests (novel, fountain writer, edge, DSP, pipeline). Re-run pytest before quoting a new count.

### How to run

```powershell
# Backend tests (venv at backend/.venv)
cd "C:\Users\Adam\Desktop\Audiobooks from llms\backend"
.\.venv\Scripts\python.exe -m pytest -q

# API server (in-memory repo — no DB needed yet)
.\.venv\Scripts\python.exe -m uvicorn app.api.main:create_app --factory --reload

# Frontend
cd ..\frontend
npm run dev
# open / for landing, /scenes/demo for workspace
```

### PRD milestones

| Milestone | Status |
|---|---|
| M0 Validation (human) | OPEN — founder must run 3 scripts past 5 directors + 5 authors (product HANDOFF §11). Engineering proceeded by explicit user request. |
| M1 Skeleton | **DONE at the app layer.** Auth (JWT + PBKDF2), projects with rights attestation, script upload → Fountain/FDX parse → story graph, manual line correction. In-memory repo; Postgres models/migrations exist but are not wired to the API. |
| M2 Audio | Planning + DSP mix + Edge TTS adapter + scene pipeline exist. Missing: emotion-capable TTS (ElevenLabs / Kokoro-style), real Redis/arq workers, audio timeline UI, emotion on `AttributedLine`. |
| M3 Shot list | Schema, generation orchestration, coverage-gap regen, 6 continuity rules, grammar profiles, API, and demo UI (mock data). Missing: real LLM adapter, API↔UI wiring. |
| M4 Visual | Prompt assembly + variant selection done. Missing: image adapters, generation workers, board grid UI. |
| M5 Animatic | Assembly commands + manifest builder done. Missing: execution + player UI. |
| M6 Hardening | Cost governor, retries, idempotency, partial-failure reconciliation, SSE contract. Missing: OTel/Sentry, rate limiting. |

### Repository map

```
backend/  (Python 3.12, FastAPI, Pydantic v2)
  app/ingest/      fountain.py, fdx.py, pdf_screenplay.py, normalize.py, elements.py,
                   novel.py, fountain_writer.py
  app/nlp/         ambience.py
  app/continuity/  6 rules, registry, grammar profiles, validator
  app/shotlist/    schema, coverage, repair, generate
  app/render/      audio/ (timing, mix, dsp, pipeline, durations, model)
                   visual/ (board + charsheet prompts, variant_for)
                   animatic.py
  app/adapters/    protocols, fakes, registry, edge.py
                   LAW: no provider SDK outside this package
  app/costs/       governor, retry + idempotency_key
  app/workers/     plan.py, events.py, tasks.py (arq)
  app/api/         FastAPI factory, JWT, InMemoryRepository, routers
  app/db/          SQLAlchemy models + Alembic 0001
frontend/  (Next.js 16.3.2, TS, Tailwind v4, zustand, motion, gsap, lenis)
  app/page.tsx           animated landing
  app/scenes/demo        scene workspace
  components/landing/    Hero, PipelineStory, FeatureGrid, NumbersStrip,
                         FooterCta, SmoothScroll
  components/            ShotListEditor, ContinuityPanel, AxisDiagram
  lib/                   types, continuity, store, api (MockApi), mock
```

---

## 5. Architectural invariants (do not violate)

- No provider SDK imported outside `app/adapters/`. Tests use fakes; never spend credits in tests.
- Cost governor `guard()` before any enqueue; adapters report `cost_cents`; caps also set provider-side.
- Terminal errors (400/403/422/policy) never retried; 429/5xx retried with backoff.
- Parsers emit flat `RawElement` lists; only `normalize.py` builds scenes/characters.
- Screenplay attribution from cues = confidence 1.0, source `"cue"`.
- Novel conversion confidence lives on `NovelConversionResult` / `needs_review`, **not** inside the emitted Fountain. After Fountain re-parse, those lines look like cue-attributed screenplay.
- Continuity warns, never blocks (mark-as-deliberate); `validator_mode` filters severity (strict=all, lenient=no info, off=skip).
- Character variants cannot overlap (DB gist constraint; `variant_for` mirrors it in memory).
- Frontend data access only through `lib/api.ts`; UI state in zustand; optimistic findings reconcile against server findings by (rule, ordinal).
- Audio renderer requires **fidelity** (every word). Visual renderer requires **compression** (few shots). Do not leak that difference upstream into the IR.
- The IR is the product. TTS and image APIs are swappable.

---

## 6. Known gaps that match this chat's asks

### Emotions on voices

- `TTSProvider.synthesize(..., emotion: str | None, ...)` exists.
- `plan_scene_audio_render` copies `line["emotion"]` into TTS job payloads.
- `AttributedLine` in `app/ingest/elements.py` has **no `emotion` field**. Ingest does not tag line emotion.
- `render_scene_audio` calls `tts.synthesize(text, voice_id, None, {})` — emotion dropped.
- `EdgeTTSAdapter` comment: "edge-tts has no emotion control; emotion is accepted and recorded only."
- Next work: persist emotion on lines (from parentheticals, novel tags, or an LLM pass), pass it through the pipeline, and add an adapter that can actually perform it (ElevenLabs, or Edge `rate`/`pitch` as a cheap stand-in).

### Crisp audio

- DSP mix + LUFS-style normalize exists; Edge neural voices are the only real TTS.
- Pipeline falls back to a 220 Hz placeholder tone when `audio_bytes` is not decodable WAV (FakeTTS).
- No high-quality provider, no 48 kHz export path, no wavesurfer timeline, no listen-in-browser UI.

### Clean vs glass UI

- No glass / `backdrop-blur` styles remain in the frontend. Landing is dark zinc, amber CTA, editorial type.
- Scene workspace is still a dense production tool (zinc-950, mono labels). That matches "ultimately clean."
- Do not reintroduce glass unless the founder asks for another test.

### Landing graphics

- Hero expects `frontend/public/hero-story-graph.png` and it is missing. Generate or replace it before calling the landing "beautiful graphics" done.

### Book → screenplay

- Rule-based path works for tagged dialogue and two-speaker alternation. LLM repair is optional and only for low-confidence quotes.
- BookNLP (product handoff §6.1) is not integrated yet.
- No API route to upload a `.txt` novel and get Fountain / a Story Graph back.
- No UI for reviewing `UNKNOWN SPEAKER` / `needs_review` quotes.

---

## 7. Next steps, in order

Priority order should follow this chat (UI polish, emotional/crisp audio, book → screenplay), then the older infrastructure list.

1. **Fix the landing hero image** (`/hero-story-graph.png` missing). Keep Motion + GSAP + Lenis. Do not add glass.
2. **Wire emotion through audio:** add `emotion` to `AttributedLine` (parentheticals first), pass it from `pipeline.py`, and either map Edge `rate`/`pitch` or add an ElevenLabs/Kokoro adapter that can act on emotion. Keep adapters inside `app/adapters/`.
3. **Expose novel → screenplay on the API** (upload manuscript, return Fountain + `needs_review` stats), then a review UI. Keep Fountain as the interchange so the rest of the pipeline stays unchanged.
4. **Crisp listen path:** render a real scene WAV with Edge (or better), serve it, add a simple player on the demo. Then wavesurfer timeline per PRD §6.2 #4.
5. **Wire Postgres:** `SqlAlchemyRepository` + `alembic upgrade head`. Local Postgres 16. Keep `InMemoryRepository` for tests.
6. **Frontend wiring:** replace `MockApi` with fetch against `/api/v1`; upload + project pages.
7. **Workers:** Redis + arq for planned jobs; SSE via `app/workers/events.py`.
8. **PDF ingest e2e** with a real screenplay PDF fixture through `extract_word_boxes`.
9. **M0 validation** remains a human task and gates go-to-market, not engineering.

---

## 8. Known issues / notes

- `app/api` uses an in-memory repo; data does not survive restarts (until Postgres is wired).
- Loop-extension in `mix.py` uses `-stream_loop` + ceil math; equal-power crossfade looping is a noted refinement. The numpy DSP path (`dsp.loop_to_length`) is what the live pipeline uses.
- Dev JWT secret defaults to `dev-secret-change-me`; set `STORY_ENGINE_SECRET` in any deployed environment.
- `create-next-app` produced Next.js 16.3.2 (PRD says 15 — newer, no issues).
- Node 20.12.1 triggers benign `EBADENGINE` warnings from eslint deps; upgrading Node to ≥20.19 silences them.
- Edge TTS is free and keyless; it is a stand-in, not the emotional/crisp end state the founder asked for.
- Product HANDOFF risk #5: set provider-side spend caps before turning on paid TTS/LLM adapters.

---

## 9. Product thesis (from original HANDOFF — do not lose this)

The valuable asset is not TTS and not image generation. Those are commodity APIs. The valuable asset is the **story graph IR**: who is speaking, which characters exist, where/when each scene is, what they wear and carry, where the camera sits.

```
Manuscript / Screenplay
          |
          v
   INGEST + NLP  ->  STORY GRAPH (IR)
                          |
             +------------+------------+
             |                         |
             v                         v
     AUDIO RENDERER            VISUAL RENDERER
   voices, ambience, mix    shot list, continuity,
                            boards, animatic
```

Primary customer: indie authors adapting their own novels (rights + dual need for audiobook and previz). Lead with attribution accuracy and sound design, never with "AI voices."
