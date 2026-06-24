"""Performance tests for PostGIS spatial queries.

Contract:
  - BBOX (ST_Within) < 50 ms
  - Near-point (ST_DWithin) < 200 ms
  - Concurrent upserts: no integrity errors or deadlocks

Run after alembic upgrade head and the scoring task:
    pytest backend/tests/performance/ -v -s
"""

from __future__ import annotations

import asyncio
import os
import random
import time

import pytest
import pytest_asyncio
from sqlalchemy import func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://spyhop:spyhop@127.0.0.1/spyhop",
)

SAMPLE_BBOX = (-91.5, -1.5, -89.5, 0.5)
SEED_COUNT = 1_000


# ---------------------------------------------------------------------------
# Session-scoped event loop — one loop for all fixtures and tests so the
# async engine's connection pool is never "attached to a different loop".
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def async_engine():
    engine = create_async_engine(DATABASE_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def session_factory(async_engine: AsyncEngine):
    return async_sessionmaker(async_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def seed_and_warmup(
    async_engine: AsyncEngine, session_factory
):
    """Seed test vessels and warm the connection pool."""
    from spyhop.db.models import VesselPosition

    min_lon, min_lat, max_lon, max_lat = SAMPLE_BBOX

    async with session_factory() as session:
        row = await session.execute(
            text("SELECT COUNT(*) FROM vessel_positions")
        )
        existing = row.scalar() or 0

        if existing < SEED_COUNT:
            for i in range(SEED_COUNT - existing):
                lon = random.uniform(min_lon - 0.5, max_lon + 0.5)
                lat = random.uniform(min_lat - 0.5, max_lat + 0.5)
                stmt = pg_insert(VesselPosition).values(
                    mmsi=f"PERF{i:09d}",
                    name=f"Perf Vessel {i}",
                    position=func.ST_SetSRID(
                        func.ST_Point(lon, lat), 4326
                    ),
                    speed_knots=random.uniform(0, 20),
                    flag=random.choice(
                        ["CHN", "PAN", "NOR", "ECU", "USA"]
                    ),
                    vessel_type=random.choice(
                        ["trawler", "tanker", "container"]
                    ),
                    ais_gap_hours=random.uniform(0, 24),
                    loitering_hours=random.uniform(0, 48),
                    in_protected_area=random.choice([True, False]),
                    risk_score=random.uniform(0, 100),
                    data_source="test",
                ).on_conflict_do_nothing(
                    constraint="uq_vessel_positions_mmsi"
                )
                await session.execute(stmt)
            await session.commit()
            print(f"\n  Seeded {SEED_COUNT - existing} test vessels")

        # Warm pool — prevents cold-connection overhead on the first test.
        await session.execute(text("SELECT 1"))


# ---------------------------------------------------------------------------
# BBOX — ST_Within via GiST index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_bbox_query_under_50ms(session_factory):
    from spyhop.db.repository import VesselRepository

    min_lon, min_lat, max_lon, max_lat = SAMPLE_BBOX
    async with session_factory() as session:
        repo = VesselRepository(session)
        t0 = time.perf_counter()
        vessels = await repo.get_vessels_in_bbox(
            min_lon, min_lat, max_lon, max_lat
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

    assert len(vessels) > 0, "No vessels in bbox — check seed fixture"
    assert elapsed_ms < 50, (
        f"BBOX query {elapsed_ms:.1f} ms > 50 ms SLA "
        "(check idx_vessel_positions_gist)"
    )
    print(
        f"\n  BBOX ST_Within: {elapsed_ms:.2f} ms "
        f"({len(vessels)} vessels)"
    )


# ---------------------------------------------------------------------------
# Top-targets — risk_score B-tree index
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_top_targets_query_under_50ms(session_factory):
    from spyhop.db.repository import VesselRepository

    async with session_factory() as session:
        repo = VesselRepository(session)
        t0 = time.perf_counter()
        vessels = await repo.get_top_targets(limit=20)
        elapsed_ms = (time.perf_counter() - t0) * 1000

    assert len(vessels) > 0
    assert elapsed_ms < 50, (
        f"Top-targets {elapsed_ms:.1f} ms > 50 ms SLA"
    )
    scores = [v.risk_score for v in vessels]
    assert scores == sorted(scores, reverse=True), (
        "Results not in descending score order"
    )
    print(
        f"\n  Top-targets (idx scan): {elapsed_ms:.2f} ms "
        f"({len(vessels)} vessels)"
    )


# ---------------------------------------------------------------------------
# Near-point — ST_DWithin geography cast
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_near_point_query(session_factory):
    from spyhop.db.repository import VesselRepository

    async with session_factory() as session:
        repo = VesselRepository(session)
        t0 = time.perf_counter()
        vessels = await repo.get_vessels_near_point(
            lat=-0.5, lon=-90.5, radius_m=200_000
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000

    assert isinstance(vessels, list)
    assert elapsed_ms < 200, (
        f"Near-point {elapsed_ms:.1f} ms > 200 ms SLA"
    )
    print(
        f"\n  Near-point ST_DWithin (200 km): {elapsed_ms:.2f} ms "
        f"({len(vessels)} vessels)"
    )


# ---------------------------------------------------------------------------
# Concurrent upserts — ON CONFLICT DO UPDATE safety
# ---------------------------------------------------------------------------

@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_upserts_are_safe(session_factory):
    from spyhop.db.repository import VesselRepository

    mmsi_set = [f"CONCURRENT{i:05d}" for i in range(50)]

    async def _batch(batch: list[str]) -> None:
        async with session_factory() as session:
            repo = VesselRepository(session)
            for mmsi in batch:
                await repo.upsert_vessel({
                    "mmsi": mmsi,
                    "name": f"Concurrent {mmsi}",
                    "lat": -0.5,
                    "lon": -90.5,
                    "speed_knots": 5.0,
                    "flag": "TST",
                    "vessel_type": "trawler",
                    "ais_gap_hours": 0.0,
                    "loitering_hours": 0.0,
                    "in_protected_area": False,
                    "risk_score": 42.0,
                    "top_reason_label": "Test",
                    "reasons": [],
                    "data_source": "test",
                })
            await session.commit()

    await asyncio.gather(*[_batch(mmsi_set) for _ in range(20)])
    print("\n  Concurrent upserts: 20 × 50 MMSI — no errors")
