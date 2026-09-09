# Accounts, credentials and credit

What you have to sign up for before Veo, Google TTS and the agent network will
run — what each one costs, what is free, and how to turn them on.

[DEPLOY.md](DEPLOY.md) covers putting the container on Cloud Run. This covers
getting the credentials that container needs, and is also the right page for
running the paid providers locally.

---

## 1. What actually requires an account

Most of the product does not. Provider selection is credential-based: with no
`GOOGLE_CLOUD_PROJECT` set, the Google adapters are never chosen, and there is
no keyless fallback that quietly degrades.

| Capability | Needs | Without it |
|---|---|---|
| Ingest, story graph, line edits | — | works |
| Shot lists, continuity, timeline | — | works (`timing_source: "estimated"`) |
| Audio render | Cloud TTS | `503 no TTS provider configured (set GOOGLE_CLOUD_PROJECT and GOOGLE_APPLICATION_CREDENTIALS)` |
| Video render | Veo on Vertex AI | `503 no video provider configured (...)` |
| Agent network, AI judges | Gemini on Vertex AI | `503 no LLM provider configured (...)` |
| Durable media across restarts | Cloud Storage | renders live in memory, die with the container |
| Analytics dashboard | ClickHouse | dashboard degrades |

`cd frontend && npm run dev` needs none of it. Sign up only for the parts you
intend to demo.

---

## 2. There is no subscription

Nothing here is a monthly plan. Google Cloud is metered pay-as-you-go, and Veo,
Cloud TTS and Gemini are three APIs on one bill — there is no per-product tier
to buy and no seat to license.

What you do need is exactly two things:

1. A Google account.
2. **One Cloud project with billing enabled.** This is the step people miss.
   Vertex AI refuses to serve a project with no billing account attached *even
   while you are inside the free trial* — the credit is applied against a
   billing account, so the account has to exist first. A project without one
   fails with a permission error that does not mention billing.

### The app's own cap is not Google's

Story Engine carries a per-project spend cap, checked before any paid call:

```
backend/app/api/repo.py:36        cost_cap_cents: int = 15000   # $150.00
backend/app/api/routers/renders.py:184   governor.guard(project.cost_spent_cents,
                                                        project.cost_cap_cents,
                                                        estimated_cents)
```

Exceeding it is an **HTTP 402**, `Cost cap exceeded: spent Xc + estimated Yc
would exceed cap Zc`, raised from all three paid routes (`renders.py:184`,
`scenes.py:255`, `assist.py:122`). Refusals are written to ClickHouse with the
headroom that caused them.

**Know this before demo day: no API route can raise the cap.** It is a column on
the project record with no setter — `backend/tests/test_api_audio.py:132` says
so outright, and the tests mutate the object directly. If a demo hits $150 the
only ways up are a database update or a fresh project. At the standard Veo rate
below, $150 is about **46 eight-second clips** on one project.

The governor is also not a Google quota — it cannot stop spending outside this
app. Set a **budget alert on the billing account as well**; the two are
independent, and only Google's can actually turn the tap off.

---

## 3. Free credit

Verified against [cloud.google.com/free](https://cloud.google.com/free) and the
[free-features docs](https://docs.cloud.google.com/free/docs/free-cloud-features):

- **$300 Welcome credit, 90 days**, new customers only.
- Excluded from the credit, per those docs: **Gemini API in AI Studio** costs,
  generative-AI **partner** models (model-as-a-service), GPUs and TPUs for VMs,
  Marketplace, Windows Server VMs, quota-increase requests, VMware Engine.
- First-party Vertex AI models — Gemini, Veo, Cloud TTS — are **not** on that
  exclusion list. Note the distinction the list is drawing: *AI Studio* is
  excluded, *Vertex AI* is not, and this app talks to Vertex
  (`GOOGLE_GENAI_USE_VERTEXAI` is forced on at the ADK seam).

Always-free monthly allowances that this app lands inside of:

| Product | Always free per month |
|---|---|
| Cloud Run | 2M requests, 240,000 vCPU-s, 450,000 GiB-s |
| Cloud Storage | 5 GB-months regional (US), 5,000 Class A + 50,000 Class B ops |
| Cloud TTS | 4M characters standard voices; 1M characters WaveNet |

### Two cautions on the free tier

**The TTS free tier may not cover the voice this app defaults to.** The default
is `gemini-2.5-flash-tts` (`backend/app/adapters/google_tts.py:51`), a premium
Gemini-TTS voice. The published free buckets are for *standard* and *WaveNet*
voices; I could not confirm from Google's pricing page whether Gemini-TTS falls
into either. Assume it is billed, or pin a standard voice for dry runs:

```bash
GOOGLE_TTS_MODEL=<a standard voice>   # check the supported-voices list
```

**The deploy config in DEPLOY.md costs about $98/month while idle.** That is not
a bug — `--min-instances 1 --no-cpu-throttling` is deliberate, because the
analytics buffer drains on a background task that Cloud Run would otherwise
freeze between requests. But it means an always-allocated 2 vCPU / 2 GiB
instance, billed for wall-clock time rather than request time:

```
vCPU  2 × 2,592,000 s  = 5,184,000  − 240,000 free = 4,944,000 × $0.000018 ≈ $89
mem   2 × 2,592,000 s  = 5,184,000  − 450,000 free = 4,734,000 × $0.000002 ≈  $9
                                                                       total ≈ $98/mo
```

≈ $3.30/day with nobody using it. Left running for a full 90-day trial that is
most of the $300, spent on an idle container. **For a demo, bring
`--min-instances 1` up for the judging window and drop it back to 0 after** —
the tradeoff is a cold start on the first request and some buffered analytics
lost on scale-down.

### What $300 actually buys

The adapter carries its own rate table, which is what the cost governor uses to
estimate and refuse *before* calling out (`backend/app/adapters/veo.py:78-85`):

| Model | App's rate | 8-second clip | Clips per $300 |
|---|---|---|---|
| `veo-3.1-generate-001` (default) | 40¢/s | $3.20 | ~93 |
| `veo-3.1-fast-generate-001` | 10¢/s | $0.80 | ~375 |

Cloud TTS is estimated at 0.002¢/character (`google_tts.py:268`) — about $20 per
million characters, so a feature-length script is cents, not dollars. **Video is
the only thing here that can eat the credit.**

Treat those as the app's *estimates*, not Google's invoice: they are constants
in this repo, they drive the 402 refusal, and they can drift from real pricing.
Per-unit prices move too often to freeze into a repo, so read them live at
[Vertex AI generative-AI
pricing](https://cloud.google.com/vertex-ai/generative-ai/pricing) before
budgeting a render run — and reconcile against the billing console after.

---

## 4. Install the SDK

Nothing in DEPLOY.md runs without the CLI. Get it from
[cloud.google.com/sdk/docs/install](https://cloud.google.com/sdk/docs/install),
then:

```bash
gcloud init                     # log in, pick or create the project
gcloud config set project YOUR_PROJECT_ID
gcloud billing projects describe YOUR_PROJECT_ID   # confirm billing is attached
```

If that last command reports `billingEnabled: false`, attach a billing account
in the console before going further; every step below will fail without it.

On Windows the installer adds its `bin` directory to the **user** PATH, so a
terminal that was already open when you installed will not find `gcloud` — and
neither will an editor started before the install, since its integrated
terminal inherits the editor's environment. Restart the shell, or the editor,
rather than concluding the install failed.

---

## 5. Enable the APIs

```bash
gcloud services enable \
    aiplatform.googleapis.com \
    texttospeech.googleapis.com \
    storage.googleapis.com \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com
```

`aiplatform` is the one that carries both Gemini and Veo; `texttospeech` is
billed and quota'd separately from it.

---

## 6. Credentials for local runs

On Cloud Run you attach a service account and ship no key (DEPLOY.md §2).
Locally, use Application Default Credentials — no key file, nothing to leak:

```bash
gcloud auth application-default login
```

Then in `backend/.env`, set only the project and region:

```bash
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

Leave `GOOGLE_APPLICATION_CREDENTIALS` blank — it only points at an explicit
service-account key file, which you should not need. Worth knowing: **no app
code ever reads it.** The three provider checks test `GOOGLE_CLOUD_PROJECT`
alone (`deps.py:83`, `:106`, `:126`); `GOOGLE_APPLICATION_CREDENTIALS` is named
in the error text for the reader's benefit and consumed by `google-auth`
itself. So a project set with no working credentials passes the check and fails
later, at call time, with *"no Google Cloud credentials found; set
GOOGLE_APPLICATION_CREDENTIALS to a service-account key file or run gcloud auth
application-default login"* (`google_auth.py:73-76`).

There is **no API-key path anywhere in the backend** — ADC only.

`.env` is gitignored and `.dockerignore` keeps it out of images, but a key file
on disk is still a key file.

---

## 7. Turning on Google TTS

Nothing to request; the API is on as soon as it is enabled and billing exists.

```bash
GOOGLE_TTS_MODEL=            # default: gemini-2.5-flash-tts
GOOGLE_TTS_LANGUAGE_CODE=    # default: en-US
```

**Newer models are a drop-in, and the id needs its suffix.** The adapter passes
`GOOGLE_TTS_MODEL` straight through as the request's `modelName`, so moving to a
newer Gemini-TTS model is one environment variable and no code change. What
costs an afternoon is the naming: the model is `gemini-3.1-flash-tts-preview`,
and `gemini-3.1-flash-tts` — the name without the suffix — answers a flat 404
that reads exactly like "this model does not exist". Verified reachable from
this project on three surfaces:

| Model | Cloud TTS `text:synthesize` | Vertex `generateContent` |
|---|---|---|
| `gemini-2.5-flash-tts` | yes | yes (`global`) |
| `gemini-2.5-pro-tts` | yes | yes (`global`) |
| `gemini-3.1-flash-tts-preview` | yes | yes (`global`, `us-central1`) |

The emotion path survives the move: `input.prompt` is accepted, and the curated
voices resolve unchanged, so the casting judge's tone still drives delivery.
This deployment runs 3.1, which rendered the sample scene in half the time 2.5
took. It is a **preview** model — that is the whole of the risk in using it.

One trap when probing: firing these requests back to back earns a per-minute
quota rejection that arrives as `400` with a *content policy* message, which
reads like a refused prompt rather than a rate limit. Space the calls a few
seconds apart before concluding anything about what a model will not say.

**There is no default voice.** `voice_id` is a required argument, chosen from
eight curated Gemini-TTS voices (`google_tts.py:147-156`) — Charon, Iapetus,
Puck, Enceladus, Kore, Aoede, Leda, Callirrhoe. `list_voices` is static and
makes no network call, so the casting UI works before any credential exists.
Twelve emotion styles map to natural-language prompts (`google_tts.py:70-83`)
and are asserted at import to match `app.nlp.emotion.EMOTIONS`.

Verify end to end:

```bash
curl -X POST "$URL/api/v1/projects/$PROJECT/scenes/1/render/audio" \
     -H "Authorization: Bearer $TOKEN"
```

A missing credential comes back as **503** whose detail names the variables to
set (`app/api/deps.py:83-88`), not a blank 500. The split is deliberate:
`app/api/main.py:106-119` maps a terminal provider error to 503 — *this
deployment cannot do it* — and a retryable one to 502 — *the upstream provider
failed*. Worth showing a judge rather than hiding.

---

## 8. Turning on Veo

Veo is region-pinned, so `GOOGLE_CLOUD_LOCATION=us-central1` is not cosmetic.
The default model is:

```
backend/app/adapters/veo.py:66    _DEFAULT_MODEL = "veo-3.1-generate-001"
```

Two things to check before demo day:

1. **Confirm the model is callable in your project.** Open Veo in
   [Model Garden](https://console.cloud.google.com/vertex-ai/model-garden) and
   confirm it is available rather than gated — Veo has historically shipped
   behind an allowlist during preview stages, and whether a given variant needs
   one changes with the release. This is the single most likely thing to be
   broken on the day, because it fails at call time, not at deploy time.
2. **The 3.0 endpoints are retired** — the adapter says so itself at
   `veo.py:64-65`: `veo-3.0-generate-001` "reached its retirement date on
   2026-06-30 and must not be used as a default." The 3.0 rows left in the cost
   table (`veo.py:80-81`) are legacy pricing only. Do not "fix" a failing render
   by pinning `GOOGLE_VEO_MODEL` back to a 3.0 id.
3. **Duration is snapped, and ties round down.** Only 4, 6 and 8 seconds are
   allowed (`veo.py:69`) while the request default is 5 (`veo.py:258`) — so an
   unspecified duration renders **4** seconds, not 5.

Cheaper variants exist for iterating; the adapter already knows them:

```bash
GOOGLE_VEO_MODEL=veo-3.1-fast-generate-001    # priced well below the standard model
```

Video is the most expensive thing this app does — per-second, not per-token.
Render one shot before rendering a scene.

Optionally have Veo write straight to a bucket instead of returning bytes
through the app:

```bash
GOOGLE_VEO_STORAGE_URI=gs://your-bucket/veo
```

---

## 9. Verify the whole thing

```bash
curl -s "$URL/api/v1/agent/network"
```

It reports `installed`, `project_configured` and `available` **separately**, so
a `false` tells you which half is missing instead of just failing. That single
call is the fastest proof that credentials actually resolved.

---

## 10. Before you walk away

- Set a **budget alert** on the billing account. The app's `cost_cap_cents`
  governs this app only.
- Drop `--min-instances` back to 0 when the demo window closes (§3).
- The $300 credit expires 90 days after signup whether or not it is spent, and
  the trial does not roll into paid billing without you confirming it.
