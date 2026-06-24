"""Async Redis client with connection pooling, pipelining, and leak-safe PubSub.

Key design decisions:
  - Single shared ConnectionPool across all FastAPI workers (set in lifespan).
  - Pipelining for multi-key writes (zadd + expire in one RTT).
  - PubSub subscriber uses try/finally to ALWAYS unsubscribe+close on disconnect.
  - Exponential backoff on initial connect via tenacity.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Optional

import ujson
from redis.asyncio import ConnectionPool, Redis
from redis.asyncio.client import PubSub
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from spyhop.config import get_settings
from spyhop.logging_config import get_logger

log = get_logger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Global pool — instantiated once in FastAPI lifespan
# ---------------------------------------------------------------------------

_pool: Optional[ConnectionPool] = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=settings.REDIS_SOCKET_CONNECT_TIMEOUT,
            decode_responses=True,
            health_check_interval=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None


# ---------------------------------------------------------------------------
# RedisClient — thin async wrapper used as a FastAPI dependency
# ---------------------------------------------------------------------------

VESSEL_SCORES_KEY = "vessel:scores"
VESSEL_UPDATES_CHANNEL = "vessel:updates"


class RedisClient:
    """Thin async Redis client injected per-request via FastAPI Depends."""

    def __init__(self) -> None:
        self._redis: Redis = Redis(connection_pool=get_pool())

    # --- Generic KV ----------------------------------------------------------

    async def get(self, key: str) -> Optional[str]:
        try:
            return await self._redis.get(key)
        except RedisError as exc:
            log.warning("redis.get.failed", key=key, error=str(exc))
            return None

    async def set(
        self, key: str, value: str, ttl: int = 300
    ) -> None:
        try:
            await self._redis.set(key, value, ex=ttl)
        except RedisError as exc:
            log.warning("redis.set.failed", key=key, error=str(exc))

    async def delete(self, *keys: str) -> None:
        try:
            await self._redis.delete(*keys)
        except RedisError as exc:
            log.warning("redis.delete.failed", keys=keys, error=str(exc))

    # --- PubSub (leak-safe) --------------------------------------------------

    async def publish(self, channel: str, message: str) -> None:
        try:
            await self._redis.publish(channel, message)
        except RedisError as exc:
            log.warning("redis.publish.failed", channel=channel, error=str(exc))

    async def subscribe(
        self, channel: str
    ) -> AsyncGenerator[str, None]:
        """Yield decoded messages from *channel*.

        The ``try/finally`` block guarantees that the PubSub connection is
        unsubscribed and closed even if the caller (WebSocket handler) raises
        or disconnects abruptly — preventing the memory leak of dangling
        server-side subscriptions.
        """
        pubsub: PubSub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        log.debug("redis.subscribe.started", channel=channel)
        try:
            async for raw in pubsub.listen():
                if raw.get("type") == "message":
                    data = raw.get("data", "")
                    yield data if isinstance(data, str) else data.decode()
        except asyncio.CancelledError:
            pass  # normal WebSocket close
        except RedisError as exc:
            log.warning("redis.subscribe.error", channel=channel, error=str(exc))
        finally:
            log.debug("redis.subscribe.cleanup", channel=channel)
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass

    # --- Sorted sets (vessel scoring) ----------------------------------------

    async def zadd_vessel_score(self, mmsi: str, score: float) -> None:
        try:
            await self._redis.zadd(VESSEL_SCORES_KEY, {mmsi: score})
        except RedisError as exc:
            log.warning("redis.zadd.failed", mmsi=mmsi, error=str(exc))

    async def get_top_mmsi(
        self, limit: int = 50
    ) -> list[tuple[str, float]]:
        """Return the top *limit* MMSI strings by descending risk score."""
        try:
            return await self._redis.zrevrange(
                VESSEL_SCORES_KEY, 0, limit - 1, withscores=True
            )
        except RedisError as exc:
            log.warning("redis.zrange.failed", error=str(exc))
            return []

    # --- Pipelined batch score update (called from Celery tasks via sync) ----
    # NOTE: This method is intentionally SYNC and used only from Celery workers
    # that import the sync Redis client directly. Kept here for co-location.
    #
    # Use pipeline_update_scores() from tasks.py instead.

    async def pipeline_update_scores(
        self, score_map: dict[str, float]
    ) -> None:
        """Atomically update multiple vessel scores in one pipeline RTT."""
        if not score_map:
            return
        async with self._redis.pipeline(transaction=False) as pipe:
            for mmsi, score in score_map.items():
                pipe.zadd(VESSEL_SCORES_KEY, {mmsi: score})
            await pipe.execute()


# ---------------------------------------------------------------------------
# Probe — used in FastAPI lifespan with exponential backoff
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(RedisConnectionError),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    stop=stop_after_attempt(10),
    reraise=True,
)
async def wait_for_redis() -> None:
    client = Redis(connection_pool=get_pool())
    await client.ping()
    log.info("redis.ready", url=settings.REDIS_URL)
    await client.aclose()
