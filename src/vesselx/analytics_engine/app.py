"""VesselX analytics engine — heatmap, EEZ risk, anomaly, and corridor endpoints."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import asyncpg
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from spyhop.analytics.corridor import (
    DARK_GAP_THRESHOLD_HOURS,
    compute_dark_gaps,
    h3_cell_center,
)
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
                COUNT(*) FILTER (WHERE risk_score >= 0.70) AS high_risk,
                COUNT(*) FILTER (WHERE risk_score >= 0.40 AND risk_score < 0.70) AS med_risk,
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


@app.get("/corridors/h3")
async def corridors_h3(
    weeks: int = Query(4, ge=1, le=52, description="Number of recent weeks to include"),
    min_score: float = Query(0.0, ge=0.0, description="Minimum corridor_score to return"),
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    """Return H3 res-5 structural risk corridor cells.

    Each cell carries a ``corridor_score`` that combines high-risk vessel
    density, dark vessel count, rendezvous events, and MPA incursions,
    multiplied by sqrt(persistence_weeks).  Cells that recur across many
    weeks score highest — that's the structural corridor signal.

    Response shape:
      cells: list of {h3_cell, lat, lon, corridor_score, persistence_weeks,
                       vessel_count, high_risk_count, dark_vessel_count,
                       dominant_flag, dominant_vessel_type, week_start}
    """
    if _pool is None:
        return {"cells": [], "weeks": weeks}
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                h3_cell, week_start,
                vessel_count, high_risk_count, med_risk_count,
                dark_vessel_count, rendezvous_count, mpa_incursion_count,
                avg_risk_score, max_risk_score,
                dominant_flag, dominant_vessel_type,
                persistence_weeks, corridor_score
            FROM h3_risk_corridors
            WHERE week_start >= (CURRENT_DATE - ($1 * INTERVAL '1 week'))
              AND corridor_score >= $2
            ORDER BY corridor_score DESC
            LIMIT $3
            """,
            weeks,
            min_score,
            limit,
        )

    cells = []
    for r in rows:
        lat, lon = h3_cell_center(r["h3_cell"])
        cells.append({
            "h3_cell": r["h3_cell"],
            "lat": lat,
            "lon": lon,
            "week_start": str(r["week_start"]),
            "corridor_score": round(float(r["corridor_score"]), 3),
            "persistence_weeks": r["persistence_weeks"],
            "vessel_count": r["vessel_count"],
            "high_risk_count": r["high_risk_count"],
            "med_risk_count": r["med_risk_count"],
            "dark_vessel_count": r["dark_vessel_count"],
            "rendezvous_count": r["rendezvous_count"],
            "mpa_incursion_count": r["mpa_incursion_count"],
            "avg_risk_score": round(float(r["avg_risk_score"]), 1),
            "max_risk_score": round(float(r["max_risk_score"]), 1),
            "dominant_flag": r["dominant_flag"],
            "dominant_vessel_type": r["dominant_vessel_type"],
        })

    return {"cells": cells, "count": len(cells), "weeks": weeks}


@app.get("/corridors/dark-gaps")
async def corridors_dark_gaps(
    gap_hours: float = Query(
        DARK_GAP_THRESHOLD_HOURS, ge=1.0, le=72.0,
        description="Minimum AIS gap hours to qualify as a dark transit",
    ),
    implausible_only: bool = Query(
        False,
        description="If true, return only gaps where implied speed > 30 kn",
    ),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """Derive dark-transit vectors from vessel_tracks.

    Finds consecutive pings per MMSI where the inter-ping gap exceeds
    ``gap_hours``, then computes the haversine displacement and implied
    speed.  Results are binned to H3 res-5 departure and arrival cells.

    A gap with implied_speed_kn > 30 is almost certainly positional
    manipulation — the vessel was physically somewhere else during the
    dark window.  Use ``implausible_only=true`` to surface only those.

    Response shape:
      gaps: list of GeoJSON-style features with properties
            {mmsi, from_h3_5, to_h3_5, gap_hours, displacement_nm,
             implied_speed_kn, implausible, dark_start, dark_end}
    """
    if _pool is None:
        return {"gaps": [], "count": 0}

    async with _pool.acquire() as conn:
        # Pull last 7 days of tracks, ordered per vessel
        rows = await conn.fetch(
            """
            SELECT
                mmsi,
                ST_Y(position::geometry) AS lat,
                ST_X(position::geometry) AS lon,
                timestamp
            FROM vessel_tracks
            WHERE timestamp >= NOW() - INTERVAL '7 days'
            ORDER BY mmsi, timestamp
            LIMIT 50000
            """,
        )

    # Group into per-MMSI sequences
    from collections import defaultdict as _dd  # noqa: PLC0415
    buckets: dict[str, list[dict]] = _dd(list)
    for r in rows:
        buckets[r["mmsi"]].append({
            "mmsi": r["mmsi"],
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "timestamp": r["timestamp"],
        })

    all_gaps = []
    for mmsi_tracks in buckets.values():
        all_gaps.extend(compute_dark_gaps(mmsi_tracks, threshold_hours=gap_hours))

    if implausible_only:
        all_gaps = [g for g in all_gaps if g.implausible]

    # Sort by gap_hours descending (longest dark windows first)
    all_gaps.sort(key=lambda g: g.gap_hours, reverse=True)
    all_gaps = all_gaps[:limit]

    features = []
    for g in all_gaps:
        features.append({
            "mmsi": g.mmsi,
            "from_lat": g.from_lat,
            "from_lon": g.from_lon,
            "to_lat": g.to_lat,
            "to_lon": g.to_lon,
            "from_h3_5": g.from_h3_5,
            "to_h3_5": g.to_h3_5,
            "gap_hours": g.gap_hours,
            "displacement_nm": g.displacement_nm,
            "implied_speed_kn": g.implied_speed_kn,
            "implausible": g.implausible,
            "dark_start": g.dark_start.isoformat(),
            "dark_end": g.dark_end.isoformat(),
        })

    return {"gaps": features, "count": len(features)}
