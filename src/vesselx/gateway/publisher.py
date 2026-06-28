"""Redis Streams publisher — gateway outbound telemetry queue.

All telemetry sources (NMEA TCP, satellite webhooks, AIS WebSocket) funnel
through publish_raw(). Downstream consumers read from the stream using
XREADGROUP so that multiple spatial-worker replicas can share the load.

Stream key schema
-----------------
vesselx:telemetry:raw      raw ingest packets from every adapter
vesselx:connectivity       heartbeat key; absent = ship is offline
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import redis.asyncio as aioredis
import ujson

from spyhop.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()

STREAM_RAW = "vesselx:telemetry:raw"
CONNECTIVITY_KEY = "vesselx:connectivity"
CONNECTIVITY_TTL = 30   # seconds; expires naturally when Starlink drops
_MAXLEN = 100_000

_pool: aioredis.ConnectionPool | None = None


def _get_pool() -> aioredis.ConnectionPool:
    global _pool
    if _pool is None:
        _pool = aioredis.ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=20,
            decode_responses=True,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
        )
    return _pool


async def publish_raw(payload: dict[str, Any]) -> str:
    """Append one telemetry record to the raw ingestion stream.

    Returns the Redis stream message ID (e.g. ``1717000000000-0``).
    Raises on Redis failure — callers log and discard rather than block the
    ingest loop; the offline buffer in vesselx.offline catches packets when
    the cloud link is down.
    """
    r = aioredis.Redis(connection_pool=_get_pool())
    msg_id: str = await r.xadd(
        STREAM_RAW,
        {"data": ujson.dumps(payload)},
        maxlen=_MAXLEN,
        approximate=True,
    )
    return msg_id


async def is_online() -> bool:
    """True when the connectivity watchdog has written a recent heartbeat."""
    try:
        r = aioredis.Redis(connection_pool=_get_pool())
        return bool(await r.exists(CONNECTIVITY_KEY))
    except Exception:
        return False


async def connectivity_watchdog(interval: float = 15.0) -> None:
    """Background task: touch the connectivity key on a fixed cadence.

    When Starlink drops, the key expires after CONNECTIVITY_TTL seconds and
    all services detect the offline transition by checking is_online().
    """
    while True:
        try:
            r = aioredis.Redis(connection_pool=_get_pool())
            await r.set(CONNECTIVITY_KEY, "1", ex=CONNECTIVITY_TTL)
            log.debug("gateway.connectivity: online")
        except Exception as exc:
            log.warning("gateway.connectivity: offline (%s)", exc)
        await asyncio.sleep(interval)


async def close() -> None:
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None
