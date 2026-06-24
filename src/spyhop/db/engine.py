"""Async SQLAlchemy engine + session factory with resilient connection pooling."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
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


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Engine — built lazily so import-time failures don't crash the worker boot
# ---------------------------------------------------------------------------

_engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    pool_pre_ping=True,   # verify connection health before lending from pool
    echo=False,
    # Pass connect_args for asyncpg statement cache
    connect_args={
        "statement_cache_size": 200,
        "command_timeout": 60,
    },
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# Resilient startup probe — retries with exponential backoff
# ---------------------------------------------------------------------------

@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=1, min=1, max=60),
    stop=stop_after_attempt(12),
    reraise=True,
)
async def wait_for_db() -> None:
    """Probe the DB until it's reachable (used in FastAPI lifespan)."""
    from sqlalchemy import text
    async with _engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    log.info("db.ready", url=settings.DATABASE_URL.split("@")[-1])


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency: yields a session, always closes it."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
