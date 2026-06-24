"""FastAPI dependency providers — DB session, Redis client, etc."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from spyhop.cache.redis_client import RedisClient
from spyhop.db.engine import get_async_session
from spyhop.db.repository import VesselRepository


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async SQLAlchemy session from the connection pool."""
    async for session in get_async_session():
        yield session


async def get_redis() -> RedisClient:
    """Return a RedisClient backed by the global connection pool."""
    return RedisClient()


async def get_vessel_repo(
    db: AsyncSession = Depends(get_db),
) -> VesselRepository:
    """Yield a VesselRepository bound to the request's DB session."""
    return VesselRepository(db)
