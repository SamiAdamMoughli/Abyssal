"""Vessel API routes — backed by PostGIS spatial queries.

All reads go through VesselRepository, which uses async SQLAlchemy + asyncpg.
A Redis cache layer sits in front of the DB for repeated identical bbox queries.
Cache TTL is intentionally short (60 s) to keep data fresh without hammering
the DB — the Celery beat task refreshes vessel data every 5 minutes.
"""

from __future__ import annotations

from typing import Optional

import ujson
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from spyhop.api.deps import get_db, get_redis, get_vessel_repo
from spyhop.api.schemas import BboxParams, TopTargetsResponse, VesselListResponse, VesselSchema
from spyhop.cache.redis_client import RedisClient
from spyhop.config import get_settings
from spyhop.db.repository import VesselRepository
from spyhop.logging_config import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["vessels"])
settings = get_settings()


def _cache_key_bbox(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> str:
    return f"vessels:bbox:{min_lon:.4f}:{min_lat:.4f}:{max_lon:.4f}:{max_lat:.4f}"


@router.get("/vessels", response_model=VesselListResponse)
async def get_vessels(
    min_lat: float = Query(..., ge=-90, le=90, description="Bounding box minimum latitude"),
    max_lat: float = Query(..., ge=-90, le=90, description="Bounding box maximum latitude"),
    min_lon: float = Query(..., ge=-180, le=180, description="Bounding box minimum longitude"),
    max_lon: float = Query(..., ge=-180, le=180, description="Bounding box maximum longitude"),
    repo: VesselRepository = Depends(get_vessel_repo),
    redis: RedisClient = Depends(get_redis),
) -> VesselListResponse:
    """All vessels inside the given bounding box — optimised via PostGIS ST_Within.

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

    vessels = await repo.get_vessels_in_bbox(min_lon, min_lat, max_lon, max_lat)
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
    await redis.set(cache_key, ujson.dumps(response.model_dump(mode="json")), ttl=60)
    return response


@router.get("/vessels/near", response_model=VesselListResponse)
async def get_vessels_near_point(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(default=50.0, gt=0, le=5000),
    repo: VesselRepository = Depends(get_vessel_repo),
) -> VesselListResponse:
    """Vessels within *radius_km* kilometres of a point — uses ST_DWithin."""
    vessels = await repo.get_vessels_near_point(lat, lon, radius_km * 1000)
    vessel_schemas = [VesselSchema(**v.to_dict()) for v in vessels]
    return VesselListResponse(
        source=settings.DATA_SOURCE,
        count=len(vessel_schemas),
        vessels=vessel_schemas,
    )


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
