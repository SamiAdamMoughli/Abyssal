"""H3 hexagonal grid API routes.

Endpoints:
  GET /api/h3/polyfill        — bbox → list of H3 cell IDs
  GET /api/h3/cells           — cell IDs + per-cell vessel counts
  GET /api/h3/context         — environmental + biological context per cell
"""

from __future__ import annotations

from typing import Any

import h3
import ujson
from fastapi import APIRouter, Depends, Query

from spyhop.api.deps import get_redis, get_vessel_repo
from spyhop.cache.redis_client import RedisClient
from spyhop.db.repository import VesselRepository
from spyhop.logging_config import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/h3", tags=["h3"])

_DEFAULT_RES = 7   # ~5 km² per hexagon — good granularity for maritime patrol
_MAX_CELLS = 500   # max empty cells returned; occupied cells are never capped


def _bbox_to_cells(
    min_lat: float, max_lat: float,
    min_lon: float, max_lon: float,
    resolution: int,
) -> list[str]:
    """Convert a bounding box to all H3 cells that cover it."""
    outer = [
        (max_lat, min_lon),
        (max_lat, max_lon),
        (min_lat, max_lon),
        (min_lat, min_lon),
    ]
    poly = h3.LatLngPoly(outer)
    return list(h3.h3shape_to_cells(poly, resolution))


@router.get("/polyfill")
def h3_polyfill(
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
    resolution: int = Query(default=_DEFAULT_RES, ge=1, le=10),
) -> dict[str, Any]:
    """Return H3 cells at *resolution* covering the bbox (capped 500)."""
    all_cells = _bbox_to_cells(min_lat, max_lat, min_lon, max_lon, resolution)
    cells = all_cells[:_MAX_CELLS]
    log.info("h3.polyfill", count=len(cells), resolution=resolution)
    return {"resolution": resolution, "count": len(cells), "cells": cells}


@router.get("/cells")
async def h3_cells_with_counts(
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
    resolution: int = Query(default=_DEFAULT_RES, ge=1, le=10),
    repo: VesselRepository = Depends(get_vessel_repo),
) -> dict[str, Any]:
    """H3 cells + per-cell vessel counts.

    Occupied cells are always included; empty cells fill up to _MAX_CELLS.
    """
    all_cells = _bbox_to_cells(min_lat, max_lat, min_lon, max_lon, resolution)
    counts = await repo.get_h3_vessel_counts(all_cells, display_resolution=resolution)

    occupied_ids = set(counts.keys())
    empty_cells = [c for c in all_cells if c not in occupied_ids]
    budget = max(0, _MAX_CELLS - len(occupied_ids))
    display_cells = list(occupied_ids) + empty_cells[:budget]

    features = []
    for cell_id in display_cells:
        boundary = h3.cell_to_boundary(cell_id)
        features.append({
            "cell_id": cell_id,
            "vessel_count": counts.get(cell_id, 0),
            "boundary": [[lat, lng] for lat, lng in boundary],
        })

    log.info(
        "h3.cells",
        total_cells=len(all_cells),
        occupied=len(occupied_ids),
        returned=len(features),
    )
    return {
        "resolution": resolution,
        "count": len(features),
        "features": features,
    }


@router.get("/context")
async def h3_context(
    ids: str = Query(
        ..., description="Comma-separated H3 cell IDs"
    ),
    redis: RedisClient = Depends(get_redis),
) -> dict[str, Any]:
    """Environmental + biological context for a set of H3 cells.

    Data is pre-baked every 6 hours by the compute_h3_context Celery task
    (Open-Meteo marine conditions + OBIS species presence + MPA status).
    Returns {} for cells not yet in cache — the worker fills them next cycle.
    """
    cell_ids = [c.strip() for c in ids.split(",") if c.strip()]
    if not cell_ids:
        return {"cells": {}}

    keys = [f"h3:context:{cid}" for cid in cell_ids]
    raw_values = await redis.mget(keys)

    result: dict[str, Any] = {}
    for cell_id, raw in zip(cell_ids, raw_values):
        if raw:
            try:
                result[cell_id] = ujson.loads(raw)
            except Exception:
                result[cell_id] = {}
        else:
            result[cell_id] = {}

    return {"cells": result}
