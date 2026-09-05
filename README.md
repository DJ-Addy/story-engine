# Story Engine

Turn a screenplay or novel into a **Story Graph IR**, then render that one
representation into an immersive multi-voice audiobook *and* a previsualization
package — shot list, continuity report, timeline, animatic, video.

**The IR is the product.** Audio and video are renderers over the same graph, AI
judges score those renders, the timeline edits the graph, and every run is
instrumented. Swap a renderer and nothing else moves.

```
        Screenplay / Novel
                │
                ▼
        INGEST + NLP  ──────►  STORY GRAPH (IR)
                                    │
              ┌─────────────────────┼─────────────────────┐
              ▼                     ▼                     ▼
       AUDIO RENDERER        VISUAL RENDERER         AI JUDGES
    Google Cloud TTS         Veo on Vertex AI     voice fit, animatic
    voices, ambience,        shot list, boards,      ranking, bake-offs
    speech-bus timing        continuity, video             │
              └─────────────────────┴─────────────────────┘
                                    │
                                    ▼
                      ClickHouse  ── scores, spend, latency
```

An **agent network on Gemini** drives that pipeline, and every judge run,
render and cost decision is written to **ClickHouse** through the official MCP
server and read back by a dashboard.

---

## Google Cloud and ClickHouse at runtime

Both are imported and called in code, not named in passing. Where to look:

| What | Where it is called | Entry point |
|---|---|---|
| **Agent Development Kit** (Agent Builder's code-first surface) | `backend/app/adapters/adk.py` — the only module that imports `google.adk` | `POST /api/v1/projects/{id}/agent/run/stream` |
| **Gemini** on Vertex AI | `backend/app/adapters/gemini.py` | powers the coordinator and every specialist |
| **Google Cloud TTS** (Gemini-TTS voices) | `backend/app/adapters/google_tts.py` | `POST /api/v1/projects/{id}/scenes/{n}/render/audio` |
| **Veo** on Vertex AI | `backend/app/adapters/veo.py` | `POST /api/v1/projects/{id}/render/video` |
| **Cloud Storage** | `backend/app/storage/gcs.py` | durable audio and video renders |
| **Cloud SQL / Postgres** | `backend/app/db/repository.py` | durable projects, scripts, renders |
| **ClickHouse via MCP** | `backend/app/analytics/mcp_client.py` — the only module that imports `mcp` | writes on every judge and render; reads at `/dashboard` |

Fastest way to confirm a live deployment is really wired:

```bash
curl -s "$URL/api/v1/agent/network"
```

It reports `installed`, `project_configured` and `available` **separately**, so a
`false` tells you which half is missing rather than just failing.

---

## The agent network

A coordinator delegating to five specialists, each mapped onto a real pipeline
stage. The tools are the actual ingest, shot-list, judge and render functions —
not a parallel demo path.

```
story_director  (coordinator, Gemini)
├── script_analyst      ingest prose/screenplay into the story graph
├── shot_designer       generate shot lists, verify dialogue coverage
├── casting_director    propose a cast, score it, act on the judge's notes
├── previz_critic       score coverage, continuity, variety, pacing
└── render_planner      assemble render prompts and price the scene
```

Specs live in `backend/app/agents/` and import no SDK at all; only
`app/adapters/adk.py` turns them into ADK objects. `POST .../agent/run/stream`
emits SSE frames — `run_started`, `message`, `delegation`, `tool_call`,
`tool_result`, `run_completed` — so you can watch the delegation happen.

---

## Quick start

```bash
# Backend
cd backend
python -m venv .venv && .venv/bin/pip install -e ".[dev]"   # Windows: .venv\Scripts\pip
cp .env.example .env                                        # every var is optional
.venv/bin/uvicorn app.api.main:app --reload

# Frontend, in another shell
cd frontend && npm install && npm run dev
```

Open http://localhost:3000. **It runs with no cloud account and no credentials**
— `next dev` defaults to mock data, the repository is in-memory, and renders
stay in process. Nothing calls a provider until you configure one.

To point the UI at the live backend: `NEXT_PUBLIC_USE_MOCK_API=false npm run dev`.
A production build defaults to live, so a deployed image can never quietly serve
fixtures; the header badge always says which mode is active.

### What works without credentials

- Ingest a screenplay, browse the story graph, edit lines
- Shot lists, continuity findings, the timeline editor
- **The timeline works before anything is rendered** — onsets are planned from
  the script by the same planner the renderer uses, and the response says
  `timing_source: "estimated"` so a guess is never shown as a measurement

Rendering audio or video, the AI judges, the agent network and the analytics
dashboard each need their provider configured. Each degrades with an actionable
message naming the missing variable rather than a blank screen.

---

## Routes

| Group | Route |
|---|---|
| Auth | `POST /api/v1/auth/register`, `/auth/login` |
| Projects | `POST\|GET /api/v1/projects`, `GET /projects/{id}` |
| Script | `POST /projects/{id}/script`, `GET /graph`, `PATCH /scenes/{n}/lines/{l}` |
| Novel | `POST /projects/{id}/novel`, `/novel/preview` |
| Scenes | `POST /scenes/{n}/shotlist`, `GET /shots`, `PATCH /findings/{id}` |
| Audio | `POST /scenes/{n}/render/audio`, `GET /audio` |
| Timeline | `GET /scenes/{n}/timeline`, `POST /timeline/edits` |
| Video | `POST /projects/{id}/render/video`, `GET /render/video/{scene}/{shot}` |
| Judges | `POST /projects/{id}/judge/voices`, `/judge/animatic`, `/judge/rank/voices`, `/judge/rank/animatic` |
| Agent | `GET /api/v1/agent/network`, `POST /projects/{id}/agent/run`, `/agent/run/stream` |
| Analytics | `GET /projects/{id}/analytics/dashboard` + 8 panel routes |

Interactive docs at `/docs`.

---

## Architecture rules

These are enforced by tests, not convention:

1. **No provider SDK outside `app/adapters/`.** `google.adk`, `google.auth` and
   `mcp` each have exactly one importing module, and all are imported lazily —
   the suite collects and passes with none of them installed.
2. **Tests never hit the network and never spend credits.** HTTP is stubbed at
   the session seam; credential failure and oversize input assert *zero* HTTP
   calls, so a misconfigured deploy fails before it can bill you.
3. **The cost governor runs before any paid call**, and refusals are recorded to
   ClickHouse with the headroom that caused them.
4. **Estimates are never presented as measurements.** `timing_source` is a
   required field with no default, so a caller cannot omit its way into a lie.

```bash
cd backend && .venv/bin/python -m pytest -q     # 1118 tests
cd frontend && npm run build && npx tsc --noEmit
```

---

## Deploying

See **[DEPLOY.md](DEPLOY.md)** — one container on Cloud Run, with the service
account roles, bucket, secrets and Cloud SQL steps spelled out.

## Licence

MIT — see [LICENSE](LICENSE).
