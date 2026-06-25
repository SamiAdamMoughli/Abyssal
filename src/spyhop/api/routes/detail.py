"""GET /api/vessels/{mmsi}/detail — fully fused vessel super-object.

Cache strategy:
  - Full detail object: 1 hour (live data — events, position — changes hourly)
  - Identity sub-object: 30 days (flag/type/IMO/tonnage change on ownership
    transfer only — caching long avoids hammering GFW + Wikidata)
"""

from __future__ import annotations

import ujson
from fastapi import APIRouter, Depends, HTTPException

from spyhop.api.deps import get_redis, get_vessel_repo
from spyhop.cache.redis_client import RedisClient
from spyhop.db.repository import VesselRepository
from spyhop.enrichment.fuse import enrich
from spyhop.logging_config import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["detail"])

_TTL_DETAIL = 3_600        # 1 hour  — live position + event history
_TTL_IDENTITY = 86_400 * 30   # 30 days — flag, IMO, tonnage, type


@router.get("/vessels/{mmsi}/detail")
async def vessel_detail(
    mmsi: str,
    repo: VesselRepository = Depends(get_vessel_repo),
    redis: RedisClient = Depends(get_redis),
) -> dict:
    """Return the fused vessel detail for a single MMSI.

    Identity fields (GFW + Wikidata) are cached 30 days.
    The full response (including live position + 90-day events) is cached
    1 hour so the panel stays current during an active patrol session.
    """
    full_key = f"detail:{mmsi}"
    identity_key = f"identity:{mmsi}"

    # Fast path — full cached response
    cached = await redis.get(full_key)
    if cached:
        return ujson.loads(cached)

    vessel = await repo.get_by_mmsi(mmsi)
    if vessel is None:
        raise HTTPException(404, f"Vessel {mmsi} not found")

    vessel_dict = vessel.to_dict()

    # Inject long-lived identity cache if available
    cached_identity = await redis.get(identity_key)
    if cached_identity:
        vessel_dict["_cached_identity"] = ujson.loads(cached_identity)

    detail = await enrich(vessel_dict)

    # Persist identity fields separately with long TTL
    if detail.get("identity"):
        await redis.set(
            identity_key,
            ujson.dumps(detail["identity"]),
            ttl=_TTL_IDENTITY,
        )

    await redis.set(full_key, ujson.dumps(detail), ttl=_TTL_DETAIL)
    log.info(
        "vessel.detail.served",
        mmsi=mmsi,
        sources=detail.get("identity", {}).get("_sources", []),
    )
    return detail
