# Railway Service Configs

Each `.toml` in this directory is the canonical config for one Railway service.
The root `railway.toml` is the **default** (spatial-engine) and is kept for
Railway's auto-detection on first deploy. All others must be wired manually:

## Wiring a service to its config file

In the Railway dashboard for each service:
1. Settings → Build → **Config Path** → set to `deploy/railway/<service>.toml`
2. Save and redeploy.

## Services

| File | Railway Service Name | Port | Notes |
|---|---|---|---|
| `spatial-engine.toml` | vesselx-spatial-engine | `$PORT` | Public API, runs migrations |
| `analytics-engine.toml` | vesselx-analytics-engine | `$PORT` | Private, internal only |
| `brain.toml` | vesselx-brain | — | Celery worker |
| `sync-worker.toml` | vesselx-sync-worker | — | Celery beat — **single instance only** |
| `spatial-worker.toml` | vesselx-spatial-worker | — | Spatial job consumer |
| `brain-api.toml` | vesselx-brain-api | `$PORT` | Ops API, internal only |

## Shared environment variables

All services share the same Railway environment variable group. Required vars:

```
DATABASE_URL
SYNC_DATABASE_URL
REDIS_URL
CELERY_BROKER_URL
CELERY_RESULT_BACKEND
GFW_API_TOKEN
GFW_API_KEY
AISSTREAM_API_KEY
LOG_LEVEL
```

Use Railway's **shared variables** feature to set these once per environment
(production / staging) rather than per-service.
