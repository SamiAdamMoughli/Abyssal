"""VesselX Spatial Worker — management API.

The compute work happens in ``vesselx.spatial_worker.worker`` (the asyncio
Redis Streams consumer). This FastAPI app is the management plane: health
probe, stream-lag visibility, and worker configuration inspection.

The client-facing operational API (vessel queries, H3 endpoints, WebSocket
fan-out) lives in ``vesselx.spatial_engine.app`` and is deployed separately.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from spyhop.cache.redis_client import close_pool, get_pool, wait_for_redis
from spyhop.config import get_settings
from spyhop.logging_config import configure_logging, get_logger
from vesselx import __version__
from vesselx.spatial_worker.worker import CONSUMER, GROUP, H3_RES, STREAM_IN, STREAM_OUT

settings = get_settings()
configure_logging(settings.LOG_LEVEL)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log.info("vesselx.spatial_worker.api.starting", version=__version__)
    await wait_for_redis()
    log.info("vesselx.spatial_worker.api.ready")
    yield
    await close_pool()
    log.info("vesselx.spatial_worker.api.stopped")


app = FastAPI(
    title="VesselX Spatial Worker — Management API",
    version=__version__,
    description=(
        "Management plane for the H3 spatial resolution worker. "
        "Reports stream lag and worker configuration."
    ),
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status":  "ok",
        "service": "vesselx-spatial-worker",
        "version": __version__,
    }


@app.get("/status")
async def status() -> dict[str, object]:
    """Return stream lag and consumer group state."""
    from spyhop.cache.redis_client import get_redis

    import redis.asyncio as aioredis
    from spyhop.cache.redis_client import get_pool
    r = aioredis.Redis(connection_pool=get_pool())
    try:
        groups = await r.xinfo_groups(STREAM_IN)
        group_info = next(
            (g for g in groups if g.get("name") == GROUP), {}
        )
        stream_len = await r.xlen(STREAM_IN)
        out_len    = await r.xlen(STREAM_OUT)
    except Exception:
        group_info = {}
        stream_len = -1
        out_len    = -1

    return {
        "service":         "vesselx-spatial-worker",
        "version":         __version__,
        "consumer_group":  GROUP,
        "consumer_name":   CONSUMER,
        "h3_resolution":   H3_RES,
        "stream_in":       STREAM_IN,
        "stream_out":      STREAM_OUT,
        "stream_in_len":   stream_len,
        "stream_out_len":  out_len,
        "pending_messages": group_info.get("pending", -1),
        "last_delivered_id": group_info.get("last-delivered-id", ""),
    }
