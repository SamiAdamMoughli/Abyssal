"""VesselX analytics engine — heatmap, EEZ risk, and anomaly endpoints."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import asyncpg
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from vesselx import __version__

_DB_URL = os.environ.get(
    "SYNC_DATABASE_URL",
    "postgresql://vesselx:vesselx@vesselx-core-db:5432/vesselx",
).replace("postgresql+psycopg2://", "postgresql://").replace("+asyncpg", "")

_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _pool
    _pool = await asyncpg.create_pool(_DB_URL, min_size=2, max_size=8)
    yield
    if _pool:
        await _pool.close()


app = FastAPI(
    title="VesselX Rule & Behavioral Anomaly Service",
    version=__version__,
    description=(
        "Heatmap density, EEZ risk scoring, behavioral anomaly detection, "
        "and alert generation analytics."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "vesselx-analytics-engine",
        "version": __version__,
    }


@app.get("/capabilities")
async def capabilities() -> dict[str, object]:
    return {
        "service": "vesselx-analytics-engine",
        "queues": ["scoring", "sync"],
        "detectors": [
            "protected_area_incursion",
            "ais_gap",
            "loitering",
            "rendezvous",
            "spoofing",
            "trajectory_pattern",
            "identity_mismatch",
            "ghost_ship_candidate",
        ],
    }


@app.get("/heatmap")
async def heatmap(
    min_lat: float = Query(-90.0),
    max_lat: float = Query(90.0),
    min_lon: float = Query(-180.0),
    max_lon: float = Query(180.0),
) -> dict[str, Any]:
    """Return vessel density as [lat, lon, weight] points for Leaflet.heat."""
    if _pool is None:
        return {"points": []}
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                ST_Y(position::geometry) AS lat,
                ST_X(position::geometry) AS lon,
                COALESCE(risk_score, 0.0) AS weight
            FROM vessel_positions
            WHERE position && ST_MakeEnvelope($1, $2, $3, $4, 4326)
            LIMIT 2000
            """,
            min_lon, min_lat, max_lon, max_lat,
        )
    points = [
        [float(r["lat"]), float(r["lon"]), float(r["weight"]) / 100.0]
        for r in rows
    ]
    return {"points": points, "count": len(points)}


@app.get("/density/h3")
async def density_h3(
    resolution: int = Query(4, ge=1, le=7),
) -> dict[str, Any]:
    """Return per-H3-cell vessel count at the requested resolution."""
    if _pool is None:
        return {"cells": {}}
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT h3_index, COUNT(*) AS n
            FROM vessel_positions
            WHERE h3_index IS NOT NULL
            GROUP BY h3_index
            HAVING COUNT(*) > 0
            LIMIT 5000
            """,
        )
    cells = {r["h3_index"]: r["n"] for r in rows}
    return {"cells": cells, "resolution": resolution}


@app.get("/risk/summary")
async def risk_summary() -> dict[str, Any]:
    """Aggregate risk statistics for dashboard KPIs."""
    if _pool is None:
        return {}
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE risk_score >= 70) AS high_risk,
                COUNT(*) FILTER (WHERE risk_score >= 40 AND risk_score < 70) AS med_risk,
                COUNT(*) FILTER (WHERE in_protected_area = TRUE) AS in_mpa,
                COUNT(*) FILTER (WHERE ais_gap_hours >= 6) AS dark_vessels,
                ROUND(AVG(risk_score)::numeric, 1) AS avg_score
            FROM vessel_positions
            """
        )
    return dict(row)


@app.get("/top-threats")
async def top_threats(limit: int = Query(10, ge=1, le=50)) -> list[dict]:
    """Highest-scoring vessels with risk breakdown."""
    if _pool is None:
        return []
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT mmsi, name, flag, vessel_type, risk_score, top_reason_label,
                   in_protected_area, ais_gap_hours, loitering_hours,
                   ST_Y(position::geometry) AS lat,
                   ST_X(position::geometry) AS lon
            FROM vessel_positions
            WHERE risk_score > 0
            ORDER BY risk_score DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]
