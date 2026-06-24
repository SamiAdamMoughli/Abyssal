# 🛰️ Mission Radar

**A real-time maritime intelligence dashboard for ocean conservation.**  
Surfaces the highest-risk vessels in any ocean region — and always explains *why*.

[![Python](https://img.shields.io/badge/Python-3.12-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Vite](https://img.shields.io/badge/Vite-5-646cff?style=flat-square&logo=vite&logoColor=white)](https://vitejs.dev/)
[![PostGIS](https://img.shields.io/badge/PostGIS-3.4-336791?style=flat-square&logo=postgresql&logoColor=white)](https://postgis.net/)
[![Celery](https://img.shields.io/badge/Celery-5-37814a?style=flat-square&logo=celery&logoColor=white)](https://docs.celeryq.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

![Mission Radar — live vessel risk map](./docs/assets/demo.gif)

> **Inspired by:** Global Fishing Watch · Sea Shepherd · SkyTruth

---

## Table of Contents

- [Why](#-why)
- [Quick Start](#-quick-start)
- [Architecture](#️-architecture)
- [Features](#-features)
- [Tech Stack](#-tech-stack)
- [API Reference](#-api-reference)
- [Environment Variables](#-environment-variables)
- [Risk Scoring](#️-risk-scoring)
- [Analytics Modules](#-analytics-modules)
- [Data Sources](#-data-sources)

---

## 🌊 Why

Every day, hundreds of thousands of vessels move across the world's oceans. For a conservation NGO the challenge isn't too *little* data — it's too *much*, without prioritisation. Illegal, unreported and unregulated (IUU) fishing costs an estimated **$23 billion a year** and devastates marine ecosystems.

Mission Radar fuses live AIS streams, protected-area geometry, vessel blacklists and behavioural analytics into a single ranked list of targets. Every score carries an explanation — because a tool that says *"this ship is suspicious"* without a reason is useless in the field.

**Two hard design constraints:**

- **Zone A (automated):** vessel behaviour, spatial violations, blacklist hits — fully machine-scored.
- **Zone B (human-led):** ownership, persons, corporate structure — intentionally *not* automated. See [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 🚀 Quick Start

### Option A — Docker (full production stack, recommended)

```bash
# 1. Clone and configure
git clone https://github.com/your-username/mission-radar.git
cd mission-radar
cp .env.example .env          # fill in your API keys

# 2. Spin up all services (PostGIS · Redis · API · Celery · Flower)
docker compose up --build

# 3. Open the dashboard
open http://localhost:5173    # Vite dev server (see Option B below)
# API health check
curl http://localhost:8000/
```

### Option B — Local dev (no Docker)

#### Backend

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Needs a running PostGIS instance — use Docker just for the DB:
docker compose up db redis -d

# Run Alembic migrations
alembic upgrade head

# Start the API
uvicorn backend.app.main:app --reload --port 8000
```

#### Frontend

```bash
cd frontend-vite
npm install
cp .env.example .env.development   # VITE_API_URL=http://127.0.0.1:8000
npm run dev                         # → http://localhost:5173
```

> **No API keys?** Set `DATA_SOURCE=synthetic` in `.env`. The backend runs entirely on generated data — no GFW token required.

---

## 🏗️ Architecture

```text
┌──────────────┐   WebSocket   ┌───────────────────┐
│  aisstream   │──────────────▶│  Celery Worker     │
│  (live AIS)  │               │  ais_stream.py     │
└──────────────┘               └────────┬──────────┘
                                        │ persists pings
                                        ▼
┌──────────────┐   bbox query  ┌───────────────────┐   SSE stream  ┌──────────────────┐
│  GFW API v3  │──────────────▶│  FastAPI + PostGIS │──────────────▶│  Vite frontend   │
│  (vessels,   │               │  Risk Engine       │               │  Leaflet map     │
│   events)    │               │  Analytics Suite   │               │  ES modules      │
└──────────────┘               └───────────────────┘               └──────────────────┘
                                        │
                               ┌────────┴──────────┐
                               │  Celery Beat       │
                               │  (scheduled tasks) │
                               └───────────────────┘
```

### Key design principle

The **Risk Engine** depends only on the `Vessel` dataclass — never on a concrete data source. Swap the source adapter; the engine, scoring logic and frontend are untouched.

### Project structure

```text
mission-radar/
├── backend/app/
│   ├── main.py              # FastAPI routes (thin — no business logic)
│   ├── risk_engine.py       # Vessel dataclass · scoring rules · rank_targets()
│   ├── geo.py               # Spatial helpers, bbox, MPA intersection
│   └── transhipment_engine.py
├── src/spyhop/
│   ├── analytics/           # Six detection modules (see below)
│   │   ├── motion_profile.py
│   │   ├── spatial_risk.py
│   │   ├── trajectory.py
│   │   ├── interaction.py
│   │   ├── spoofing.py
│   │   └── context_fusion.py
│   ├── db/
│   │   ├── models.py        # SQLAlchemy ORM (VesselPosition · VesselTrack · …)
│   │   └── alembic/         # 7 versioned migrations
│   ├── worker/
│   │   ├── celery_app.py
│   │   ├── tasks.py         # ingest_vessels · score_vessel · sync_blacklists
│   │   └── ais_stream.py    # live AIS WebSocket consumer
│   └── api/
├── frontend-vite/
│   └── src/                 # 15 ES modules (map · markers · badges · api · …)
└── docker-compose.yml
```

---

## 📦 Features

- **Live vessel map** — Leaflet with vessel-type SVG icons, risk-colour coding and animated range rings
- **Real-time updates** — Server-Sent Events push data changes without client polling
- **Explainable scoring** — every risk point is backed by a human-readable badge and tooltip
- **Six analytics dimensions** — motion, spatial, trajectory, V2V encounter, AIS gap / spoofing, contextual fusion
- **MPA overlay** — protected-area polygons from WDPA / Global Forest Watch, cached 24 h in localStorage
- **Blacklist cross-reference** — CCAMLR IUU list (18 vessels) + OpenSanctions (1 922 vessels, 15 sources)
- **Bbox-based geospatial filtering** — PostGIS `ST_MakeEnvelope` + GiST index; "Search this area" button
- **Skeleton screens** — 200 ms threshold prevents flicker on fast backends
- **Shared links** — `#bbox=…&start=…&end=…` in the URL; copy and send to a colleague
- **Dual data source** — switch between synthetic (no token) and Global Fishing Watch (live AIS) per request

---

## 🛠 Tech Stack

| Layer | Technology | Why |
| :--- | :--- | :--- |
| **Frontend** | Vite 5 · ES modules · Leaflet | HMR in dev, cache-busted hashes in prod; 15-module split from 2 239-line monolith |
| **API** | FastAPI · uvicorn · sse-starlette | Async-first; SSE via `EventSourceResponse`; `~2–3 ms` on synthetic source |
| **Database** | PostgreSQL 16 + PostGIS 3.4 | Spatial queries (`ST_MakeEnvelope`, GiST index on `position`) |
| **Task queue** | Celery 5 · Redis 7 | Async AIS ingestion, score re-computation, blacklist sync |
| **Analytics** | Pure Python dataclasses | Deterministic, testable, no ML black box |
| **Containers** | Docker Compose | Six services; health-check gating, per-service resource limits |
| **Migrations** | Alembic | 7 versioned migrations, auto-apply on startup |

---

## 🔌 API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | Health check, reports active data source |
| `GET` | `/api/vessels` | All vessels in bbox with risk scores |
| `GET` | `/api/vessels/stream` | SSE stream — pushes on fingerprint change |
| `GET` | `/api/protected-areas` | WDPA MPA polygons for bbox (GeoJSON) |

All `/api/*` endpoints accept `?source=synthetic` (default) or `?source=gfw`, and bbox params `min_lat`, `max_lat`, `min_lon`, `max_lon`.

```bash
# Fetch vessels in a bbox (Galápagos)
curl "http://localhost:8000/api/vessels?min_lat=-2&max_lat=1&min_lon=-92&max_lon=-89"

# Stream live updates
curl -N "http://localhost:8000/api/vessels/stream?min_lat=-2&max_lat=1&min_lon=-92&max_lon=-89"
```

---

## 🔑 Environment Variables

Copy `.env.example` to `.env` and fill in your keys. **Never commit `.env`.**

```bash
cp .env.example .env
```

| Variable | Default | Description |
| :--- | :--- | :--- |
| `DATA_SOURCE` | `synthetic` | `synthetic` or `gfw` |
| `DATABASE_URL` | — | Async PostgreSQL URL (`postgresql+asyncpg://…`) |
| `REDIS_URL` | — | `redis://redis:6379/0` |
| `GFW_API_TOKEN` | — | Global Fishing Watch API v3 token — [get one here](https://globalfishingwatch.org/our-apis/tokens) |
| `GFW_BBOX` | Galápagos | `min_lon,min_lat,max_lon,max_lat` |
| `GFW_API_KEY` | — | Global *Forest* Watch Data API key (protected areas) |
| `AISSTREAM_API_KEY` | — | [aisstream.io](https://aisstream.io/authenticate) key for live AIS WebSocket |
| `VESSEL_CACHE_TTL` | `60` | Vessel cache TTL in seconds |
| `VESSEL_STREAM_POLL_SECONDS` | `3` | How often the SSE endpoint re-queries the DB |

> **No keys needed for local dev.** With `DATA_SOURCE=synthetic` the backend runs entirely on generated vessel data. A GFW token is only needed if you want live AIS positions over a real ocean region.

---

## ⚖️ Risk Scoring

The scoring system is intentionally **transparent and rule-based** — no black-box ML.

```text
score = Σ(points for each triggered rule), capped at 100
```

Every triggered rule produces a badge with a human-readable label and a tooltip detail. Vessels with score 0 (no rules fired) are shown on the map but not ranked.

| Rule | Points | Signal |
| :--- | ---: | :--- |
| Inside protected area (MPA) | +35 | Spatial violation |
| Fishing speed 2–5 kn | +20 | Trawl speed profile |
| AIS gap ≥ 12 h | +25 | "Going dark" |
| AIS gap ≥ 4 h | +10 | Moderate gap |
| Loitering ≥ 6 h | +15 | Possible transshipment |
| IUU blacklist hit | +40 | CCAMLR / RFMO authoritative list |
| Sanctions hit | +35 | OpenSanctions (1 922 vessels) |
| Transshipment pattern | +20 | Rendezvous + AIS dark combo |

**Validation (synthetic approximations of known cases, threshold ≥ 50):**  
4 / 5 documented IUU vessels correctly flagged (80% TP rate). The one miss — *Fu Yuan Yu Leng 999*, a reefer carrier — exposes a known gap in speed-based rules for non-fishing vessel types. See [VALIDATION_REPORT.txt](VALIDATION_REPORT.txt).

### Adding a rule

```python
# risk_engine.py — one function, one list append, nothing else changes
def rule_flag_of_convenience(v: Vessel) -> Optional[RiskReason]:
    if v.flag in HIGH_RISK_FLAGS and v.ais_gap_hours >= 4:
        return RiskReason(
            points=15,
            label="Flag of convenience + gap",
            detail="Open registry flag combined with AIS blackout.",
        )
    return None

RULES = [..., rule_flag_of_convenience]  # that's it
```

---

## 🧠 Analytics Modules

Six independent detectors run on the `vessel_tracks` sliding window and write results back to `vessel_positions`:

| Module | What it computes |
| :--- | :--- |
| `motion_profile` | Behaviour classification: `transit / trawling / loitering / anchored` + confidence |
| `spatial_risk` | Distance to nearest MPA boundary, time-in-zone, border-skirting flag |
| `trajectory` | Route geometry pattern: `grid / holding / spiral / transit / anomaly` |
| `interaction` | V2V encounter detection: partner vessel type, meeting class |
| `spoofing` | AIS gap kinematic analysis: implied speed violations, spoofing flag |
| `context_fusion` | Environmental overlay: SST, wave height, wind speed from CMEMS rasters |

Each module is a pure function over the `Vessel` dataclass — deterministic, independently testable, no shared state.

---

## 🗂 Data Sources

| Source | Licence | Status |
| :--- | :--- | :--- |
| AIS via [Global Fishing Watch API v3](https://globalfishingwatch.org/our-apis/) | GFW ToS | ✅ live (with token) |
| WDPA protected areas via [Global Forest Watch Data API](https://data-api.globalforestwatch.org/) | CC-BY | ✅ live (with key) |
| CCAMLR IUU blacklist | Official (18 vessels) | ✅ bundled |
| OpenSanctions vessels | CC-BY-NC 4.0 (1 922 vessels, 15 sources) | ✅ bundled |
| Live AIS via [aisstream.io](https://aisstream.io/) | ToS | ✅ streaming (with key) |
| Synthetic data | — | ✅ built-in default |

> **Zone A only.** Corporate ownership (OpenCorporates, ICIJ, OCCRP) and personal data sources are deliberately excluded. They belong behind a human-led analyst workspace (Zone B). See [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 📄 License

MIT — see [LICENSE](LICENSE). Data sources carry their own licences; see the table above.
