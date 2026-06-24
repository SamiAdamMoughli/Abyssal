"""Health-check endpoint — verifies DB + Redis connectivity."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spyhop.api.deps import get_db, get_redis
from spyhop.api.schemas import HealthResponse
from spyhop.cache.redis_client import RedisClient
from spyhop.config import get_settings

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/", response_model=HealthResponse)
@router.get("/health", response_model=HealthResponse)
async def health_check(
    db: AsyncSession = Depends(get_db),
    redis: RedisClient = Depends(get_redis),
) -> HealthResponse:
    """Deep health check — probes both DB and Redis."""
    db_status = "ok"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        db_status = f"error: {exc}"

    redis_status = "ok"
    try:
        pong = await redis._redis.ping()
        if not pong:
            redis_status = "no pong"
    except Exception as exc:  # noqa: BLE001
        redis_status = f"error: {exc}"

    return HealthResponse(
        status="ok" if db_status == "ok" and redis_status == "ok" else "degraded",
        service=settings.APP_NAME,
        version=settings.APP_VERSION,
        db=db_status,
        redis=redis_status,
    )
