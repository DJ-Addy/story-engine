# Deploying to Cloud Run

One container: Next.js serves `$PORT` and proxies `/api/v1` to uvicorn on
loopback. Cloud Run exposes a single port, and the frontend already rewrites
`/api/v1` to `API_PROXY_TARGET`, so the entrypoint just points that at
localhost. Nothing in the app code changes between local and deployed.

## 1. Prerequisites

```bash
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com aiplatform.googleapis.com \
    texttospeech.googleapis.com storage.googleapis.com \
    artifactregistry.googleapis.com cloudbuild.googleapis.com
```

## 2. Service account

The app authenticates with Application Default Credentials, so on Cloud Run you
attach a service account and ship no key file.

```bash
gcloud iam service-accounts create story-engine

PROJECT=$(gcloud config get-value project)
SA="story-engine@${PROJECT}.iam.gserviceaccount.com"

# Gemini, Veo and the ADK agent network all go through Vertex AI.
gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${SA}" --role="roles/aiplatform.user"
# Rendered audio and video.
gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${SA}" --role="roles/storage.objectAdmin"
# Only needed if you serve media with signed URLs rather than a public bucket.
gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${SA}" --role="roles/iam.serviceAccountTokenCreator"
```

## 3. Media bucket

```bash
gcloud storage buckets create "gs://${PROJECT}-story-engine-media" \
    --location=us-central1 --uniform-bucket-level-access
```

## 4. Secrets

Keep ClickHouse credentials and the token-signing secret out of env vars.

```bash
printf '%s' 'YOUR_CLICKHOUSE_PASSWORD' | \
    gcloud secrets create clickhouse-password --data-file=-
printf '%s' "$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" | \
    gcloud secrets create story-engine-secret --data-file=-

gcloud secrets add-iam-policy-binding clickhouse-password \
    --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding story-engine-secret \
    --member="serviceAccount:${SA}" --role="roles/secretmanager.secretAccessor"
```

## 5. Deploy

```bash
gcloud run deploy story-engine \
    --source . \
    --region us-central1 \
    --service-account "$SA" \
    --allow-unauthenticated \
    --memory 2Gi \
    --cpu 2 \
    --timeout 3600 \
    --min-instances 1 \
    --max-instances 1 \
    --no-cpu-throttling \
    --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT}" \
    --set-env-vars "GOOGLE_CLOUD_LOCATION=us-central1" \
    --set-env-vars "GCS_BUCKET=${PROJECT}-story-engine-media" \
    --set-env-vars "STORY_ENGINE_ANALYTICS_ENABLED=1" \
    --set-env-vars "CLICKHOUSE_HOST=YOUR_CLICKHOUSE_HOST" \
    --set-env-vars "CLICKHOUSE_USER=default" \
    --set-env-vars "CLICKHOUSE_SECURE=true" \
    --set-secrets "CLICKHOUSE_PASSWORD=clickhouse-password:latest" \
    --set-secrets "STORY_ENGINE_SECRET=story-engine-secret:latest"
```

### Why those flags

- `--min-instances 1` and `--no-cpu-throttling` — analytics events buffer in
  memory and drain on a background asyncio task. Cloud Run throttles CPU
  between requests by default, which stalls that drain; scaling to zero drops
  whatever is still buffered.
- `--max-instances 1` — only needed while the in-memory repository is in use.
  Once `DATABASE_URL` is set, raise it.
- `--timeout 3600` — Veo renders are submit/poll/fetch and can outlast the
  60-second default.
- `--memory 2Gi` — Python, Node and any un-uploaded media bytes share the
  instance. Drop to 1Gi once `GCS_BUCKET` is set and renders stop being held
  in memory.

## 6. Persistence (recommended before judging)

Without a database the in-memory repository loses every project on redeploy.

```bash
gcloud sql instances create story-engine-db \
    --database-version=POSTGRES_16 --tier=db-f1-micro --region=us-central1
gcloud sql databases create story_engine --instance=story-engine-db
gcloud sql users set-password postgres --instance=story-engine-db --password=YOUR_DB_PASSWORD

gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${SA}" --role="roles/cloudsql.client"
```

Then redeploy with the socket vars; the entrypoint runs `alembic upgrade head`
automatically whenever a database is configured:

```bash
gcloud run services update story-engine --region us-central1 \
    --add-cloudsql-instances "${PROJECT}:us-central1:story-engine-db" \
    --set-env-vars "CLOUD_SQL_CONNECTION_NAME=${PROJECT}:us-central1:story-engine-db" \
    --set-env-vars "DB_USER=postgres,DB_NAME=story_engine" \
    --set-secrets "DB_PASS=db-password:latest" \
    --max-instances 4
```

## 7. Verify

```bash
URL=$(gcloud run services describe story-engine --region us-central1 \
      --format='value(status.url)')

curl -s "$URL/api/v1/health"
# Shows the agent network and whether the ADK and credentials resolved.
curl -s "$URL/api/v1/agent/network" | head -40
```

`/api/v1/agent/network` is the fastest check that the deployment is genuinely
wired: it reports `installed`, `project_configured` and `available` separately,
so a false value tells you which half is missing.

## Building locally

The daemon must be running; `gcloud run deploy --source .` builds remotely and
needs no local Docker at all.

```bash
docker build -t story-engine .
docker run --rm -p 8080:8080 \
    -e GOOGLE_CLOUD_PROJECT=your-project \
    -v "$HOME/.config/gcloud:/home/appuser/.config/gcloud:ro" \
    story-engine
```
