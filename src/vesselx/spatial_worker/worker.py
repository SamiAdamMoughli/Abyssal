"""H3 spatial resolution worker.

Consumes raw telemetry from the ``vesselx:telemetry:raw`` Redis Stream,
computes the H3 hexagon cell index (resolution 7, ~5 km² — matching the
existing ``h3_index`` column on VesselPosition), upserts the live position
snapshot to PostGIS, refreshes the Redis H3 hot-state layer, and forwards the
enriched record to ``vesselx:telemetry:spatialized`` for the brain to consume.

Consumer group : spatial-workers
Consumer name  : spatial-worker-0  (increment for additional replicas)

Run this worker as a standalone process:
    python -m vesselx.spatial_worker.worker
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime, timezone
from typing import Any

import h3
import redis.asyncio as aioredis
import ujson
from geoalchemy2.functions import ST_MakePoint, ST_SetSRID
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from spyhop.config import get_settings
from spyhop.db.models import VesselPosition, VesselTrack

log      = logging.getLogger(__name__)
settings = get_settings()

STREAM_IN  = "vesselx:telemetry:raw"
STREAM_OUT = "vesselx:telemetry:spatialized"
GROUP      = "spatial-workers"
CONSUMER   = "spatial-worker-0"
H3_RES     = 7        # ~5 km² — consistent with VesselPosition.h3_index column
BATCH_SIZE = 100
BLOCK_MS   = 5_000    # block up to 5 s waiting for new messages
H3_TTL     = 300      # Redis H3 hot-layer TTL in seconds

_engine  = create_async_engine(
    settings.DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)
_Session = async_sessionmaker(_engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Consumer group bootstrap
# ---------------------------------------------------------------------------

async def _ensure_group(r: aioredis.Redis) -> None:
    """Create the consumer group if it doesn't exist yet; mkstream=True so
    the stream itself is created on first call even with no messages in it."""
    try:
        await r.xgroup_create(STREAM_IN, GROUP, id="0", mkstream=True)
        log.info("spatial_worker.group_created stream=%s group=%s", STREAM_IN, GROUP)
    except aioredis.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


# ---------------------------------------------------------------------------
# Per-message processing
# ---------------------------------------------------------------------------

async def _process(
    r: aioredis.Redis,
    session: AsyncSession,
    msg_id: str,
    fields: dict[str, str],
) -> None:
    data: dict[str, Any] = ujson.loads(fields["data"])

    lat  = data.get("lat")
    lon  = data.get("lon")
    mmsi = data.get("mmsi")

    if lat is None or lon is None or not mmsi:
        await r.xack(STREAM_IN, GROUP, msg_id)
        return

    lat, lon = float(lat), float(lon)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        await r.xack(STREAM_IN, GROUP, msg_id)
        return

    cell = h3.latlng_to_cell(lat, lon, H3_RES)
    now  = datetime.now(timezone.utc)
    geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326)

    # --- PostGIS: upsert live position snapshot ----------------------------
    pos_stmt = (
        pg_insert(VesselPosition)
        .values(
            mmsi=mmsi,
            name=data.get("name") or "",
            position=geom,
            speed_knots=data.get("sog") or 0.0,
            cog_degrees=data.get("cog") or 0.0,
            h3_index=cell,
            data_source=data.get("source", "gateway"),
        )
        .on_conflict_do_update(
            index_elements=["mmsi"],
            set_={
                "position":    geom,
                "speed_knots": data.get("sog") or 0.0,
                "cog_degrees": data.get("cog") or 0.0,
                "h3_index":    cell,
                "updated_at":  func.now(),
            },
        )
    )
    await session.execute(pos_stmt)

    # --- PostGIS: append ping to track history (feeds motion profile) ------
    track_stmt = pg_insert(VesselTrack).values(
        mmsi=mmsi,
        position=geom,
        sog=data.get("sog") or 0.0,
        cog=data.get("cog") or 0.0,
        timestamp=now,
        source=data.get("source", "gateway"),
    )
    await session.execute(track_stmt)

    await session.commit()

    # --- Redis: update H3 hot-layer (same structure as aisstream.py flush) --
    blob = ujson.dumps({
        "mmsi":      mmsi,
        "name":      data.get("name", ""),
        "lat":       lat,
        "lon":       lon,
        "sog":       data.get("sog") or 0.0,
        "h3_index":  cell,
        "source":    data.get("source", "gateway"),
        "updated_at": now.isoformat(),
    })
    await r.hset(f"h3:{cell}", mmsi, blob)
    await r.expire(f"h3:{cell}", H3_TTL)

    # --- Redis Streams: forward enriched record to brain -------------------
    data["h3_index"] = cell
    await r.xadd(
        STREAM_OUT,
        {"data": ujson.dumps(data)},
        maxlen=50_000,
        approximate=True,
    )

    await r.xack(STREAM_IN, GROUP, msg_id)
    log.debug("spatial_worker.spatialized mmsi=%s h3=%s", mmsi, cell)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

async def run() -> None:
    pool = aioredis.ConnectionPool.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        max_connections=10,
    )
    r = aioredis.Redis(connection_pool=pool)

    await _ensure_group(r)
    log.info(
        "spatial_worker.started group=%s consumer=%s h3_res=%d",
        GROUP, CONSUMER, H3_RES,
    )

    stop = asyncio.Event()
    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, stop.set)
    loop.add_signal_handler(signal.SIGINT, stop.set)

    async with _Session() as session:
        while not stop.is_set():
            try:
                results = await r.xreadgroup(
                    groupname=GROUP,
                    consumername=CONSUMER,
                    streams={STREAM_IN: ">"},
                    count=BATCH_SIZE,
                    block=BLOCK_MS,
                )
            except Exception as exc:
                log.error("spatial_worker.read_error err=%s", exc)
                await asyncio.sleep(2)
                continue

            if not results:
                continue

            for _stream_name, messages in results:
                for msg_id, fields in messages:
                    try:
                        await _process(r, session, msg_id, fields)
                    except Exception as exc:
                        log.error(
                            "spatial_worker.msg_error msg_id=%s err=%s",
                            msg_id, exc,
                        )

    await pool.aclose()
    await _engine.dispose()
    log.info("spatial_worker.stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
