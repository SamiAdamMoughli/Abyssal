"""Vessel API routes — backed by PostGIS spatial queries.

All reads go through VesselRepository, which uses async SQLAlchemy + asyncpg.
A Redis cache layer sits in front of the DB for repeated identical bbox
queries. Cache TTL is intentionally short (60 s) to keep data fresh without
hammering the DB — the Celery beat task refreshes vessel data every 5 min.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import ujson
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sse_starlette.sse import EventSourceResponse

from spyhop.api.deps import get_redis, get_vessel_repo
from spyhop.api.schemas import TopTargetsResponse, VesselListResponse, VesselSchema
from spyhop.cache.redis_client import RedisClient
from spyhop.config import get_settings
from spyhop.db.repository import VesselRepository
from spyhop.logging_config import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["vessels"])
settings = get_settings()


def _cache_key_bbox(
    min_lon: float, min_lat: float,
    max_lon: float, max_lat: float,
) -> str:
    return (
        f"vessels:bbox"
        f":{min_lon:.4f}:{min_lat:.4f}"
        f":{max_lon:.4f}:{max_lat:.4f}"
    )


@router.get("/vessels", response_model=VesselListResponse)
async def get_vessels(
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
    repo: VesselRepository = Depends(get_vessel_repo),
    redis: RedisClient = Depends(get_redis),
) -> VesselListResponse:
    """All vessels inside the given bbox — optimised via PostGIS ST_Within.

    Responses are cached in Redis for 60 s to absorb burst traffic while
    keeping data close to real-time (Celery updates every 5 min).
    """
    if min_lat >= max_lat:
        raise HTTPException(400, "max_lat must be greater than min_lat")
    if min_lon >= max_lon:
        raise HTTPException(400, "max_lon must be greater than min_lon")

    cache_key = _cache_key_bbox(min_lon, min_lat, max_lon, max_lat)
    cached = await redis.get(cache_key)
    if cached:
        return VesselListResponse(**ujson.loads(cached))

    vessels = await repo.get_vessels_in_bbox(
        min_lon, min_lat, max_lon, max_lat
    )
    vessel_schemas = [VesselSchema(**v.to_dict()) for v in vessels]

    response = VesselListResponse(
        source=settings.DATA_SOURCE,
        count=len(vessel_schemas),
        vessels=vessel_schemas,
    )
    await redis.set(
        cache_key,
        ujson.dumps(response.model_dump(mode="json")),
        ttl=settings.VESSEL_CACHE_TTL,
    )
    log.info(
        "vessels.bbox.served",
        bbox=f"{min_lon},{min_lat},{max_lon},{max_lat}",
        count=len(vessel_schemas),
        cached=False,
    )
    return response


@router.get("/targets", response_model=TopTargetsResponse)
async def get_top_targets(
    top_n: int = Query(default=5, ge=1, le=100),
    repo: VesselRepository = Depends(get_vessel_repo),
    redis: RedisClient = Depends(get_redis),
) -> TopTargetsResponse:
    """Top-N vessels by pre-computed risk score — served from PostGIS index."""
    cache_key = f"targets:top:{top_n}"
    cached = await redis.get(cache_key)
    if cached:
        return TopTargetsResponse(**ujson.loads(cached))

    vessels = await repo.get_top_targets(limit=top_n)
    vessel_schemas = [VesselSchema(**v.to_dict()) for v in vessels]

    response = TopTargetsResponse(
        source=settings.DATA_SOURCE,
        count=len(vessel_schemas),
        targets=vessel_schemas,
    )
    await redis.set(
        cache_key,
        ujson.dumps(response.model_dump(mode="json")),
        ttl=60,
    )
    return response


@router.get("/vessels/near", response_model=VesselListResponse)
async def get_vessels_near_point(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(default=50.0, gt=0, le=5000),
    repo: VesselRepository = Depends(get_vessel_repo),
) -> VesselListResponse:
    """Vessels within *radius_km* km of a point — uses ST_DWithin."""
    vessels = await repo.get_vessels_near_point(lat, lon, radius_km * 1000)
    vessel_schemas = [VesselSchema(**v.to_dict()) for v in vessels]
    return VesselListResponse(
        source=settings.DATA_SOURCE,
        count=len(vessel_schemas),
        vessels=vessel_schemas,
    )


# ---------------------------------------------------------------------------
# H3-based vessel query
# ---------------------------------------------------------------------------

@router.get("/vessels/hex", response_model=VesselListResponse)
async def get_vessels_by_h3(
    h3_ids: str = Query(..., description="Comma-separated H3 cell IDs"),
    repo: VesselRepository = Depends(get_vessel_repo),
) -> VesselListResponse:
    """Vessels whose h3_index is in the supplied set of H3 cell IDs.

    The client sends IDs from GET /api/h3/polyfill or its own selection.
    B-tree lookup on h3_index — no spatial predicate needed.
    """
    ids = [c.strip() for c in h3_ids.split(",") if c.strip()]
    vessels = await repo.get_vessels_by_h3(ids)
    schemas = [VesselSchema(**v.to_dict()) for v in vessels]
    return VesselListResponse(
        source=settings.DATA_SOURCE,
        count=len(schemas),
        vessels=schemas,
    )


# ---------------------------------------------------------------------------
# SSE stream — registered BEFORE /vessels/{mmsi} to avoid route shadowing
# ---------------------------------------------------------------------------

@router.get("/vessels/stream")
async def stream_vessels(
    request: Request,
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
    repo: VesselRepository = Depends(get_vessel_repo),
) -> EventSourceResponse:
    """SSE stream — pushes vessel updates only when the fingerprint changes.

    Polls PostGIS every VESSEL_STREAM_POLL_SECONDS. EventSource reconnects
    automatically on drop; no client-side polling logic needed.
    """
    async def generate():
        last_fp: tuple = ()
        while True:
            if await request.is_disconnected():
                break
            try:
                vessels = await repo.get_vessels_in_bbox(
                    min_lon, min_lat, max_lon, max_lat
                )
                fp = tuple(
                    (v.mmsi, round(v.risk_score or 0.0, 1))
                    for v in vessels
                )
                if fp != last_fp:
                    last_fp = fp
                    schemas = [VesselSchema(**v.to_dict()) for v in vessels]
                    payload = VesselListResponse(
                        source=settings.DATA_SOURCE,
                        count=len(schemas),
                        vessels=schemas,
                    )
                    yield {
                        "data": ujson.dumps(payload.model_dump(mode="json"))
                    }
            except Exception as exc:
                log.warning("stream.error", error=str(exc))
                yield {"event": "error", "data": str(exc)}
                break
            await asyncio.sleep(settings.VESSEL_STREAM_POLL_SECONDS)

    return EventSourceResponse(generate())


@router.get("/vessels/{mmsi}", response_model=VesselSchema)
async def get_vessel_by_mmsi(
    mmsi: str,
    repo: VesselRepository = Depends(get_vessel_repo),
) -> VesselSchema:
    """Retrieve a single vessel by MMSI."""
    vessel = await repo.get_by_mmsi(mmsi)
    if vessel is None:
        raise HTTPException(404, f"Vessel {mmsi} not found")
    return VesselSchema(**vessel.to_dict())


# ---------------------------------------------------------------------------
# Protected areas (MPA polygons for the frontend map layer)
# ---------------------------------------------------------------------------

@router.get("/protected-areas")
def get_protected_areas(
    min_lat: Optional[float] = Query(None),
    max_lat: Optional[float] = Query(None),
    min_lon: Optional[float] = Query(None),
    max_lon: Optional[float] = Query(None),
) -> Dict[str, Any]:
    """WDPA MPA polygons as GeoJSON for the map layer.

    Delegates to the backend geo module (GFW Data API with local fallback).
    Synchronous because the geo module uses requests, not httpx.
    """
    try:
        from backend.app import geo

        bbox = None
        if all(v is not None for v in (min_lat, max_lat, min_lon, max_lon)):
            bbox = (min_lat, max_lat, min_lon, max_lon)
            geo.set_area(bbox)

        fc = geo.get_protected_areas_geojson()
        source = "gfw" if geo._source() == "gfw" else "local"

        if not fc.get("features"):
            fallback = geo.local_protected_areas(bbox)
            if fallback.get("features"):
                fc = fallback
                source = "local-fallback"

        return {
            "type": fc.get("type", "FeatureCollection"),
            "features": fc.get("features", []),
            "source": source,
            "count": len(fc.get("features", [])),
        }
    except Exception as exc:
        log.warning("protected_areas.error", error=str(exc))
        return {
            "type": "FeatureCollection",
            "features": [],
            "source": "error",
            "count": 0,
        }
