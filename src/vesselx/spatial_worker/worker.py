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
H3_RES       = 7        # ~5 km² — consistent with VesselPosition.h3_index column
BATCH_SIZE   = 100
BLOCK_MS     = 5_000    # block up to 5 s waiting for new messages
H3_TTL       = 300      # Redis H3 hot-layer TTL in seconds
TRACK_MAXLEN = 20       # ring-buffer depth fed to the ML kinematic extractor

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
# Batch processing
# ---------------------------------------------------------------------------

async def _process_batch(
    r: aioredis.Redis,
    session: AsyncSession,
    messages: list[tuple[str, dict[str, str]]],
) -> int:
    """Process a full batch of stream messages in two round-trips: one DB
    transaction (bulk upsert + bulk insert) and one Redis pipeline.

    Returns the number of valid messages processed.
    """
    now     = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    pos_values:      list[dict]              = []
    track_values:    list[dict]              = []
    h3_updates:      list[tuple[str, str, str]] = []  # (cell, mmsi, blob)
    vessel_updates:  list[tuple[str, dict]]     = []  # (mmsi, field_map)
    ring_pushes:     list[tuple[str, str]]      = []  # (mmsi, track_point_json)
    parsed_data:     list[tuple[dict, str]]     = []  # (data_dict, cell) for eco pass
    valid_ids:       list[str]              = []
    skip_ids:        list[str]              = []

    for msg_id, fields in messages:
        try:
            data: dict[str, Any] = ujson.loads(fields["data"])
        except Exception:
            skip_ids.append(msg_id)
            continue

        lat  = data.get("lat")
        lon  = data.get("lon")
        mmsi = data.get("mmsi")

        if lat is None or lon is None or not mmsi:
            skip_ids.append(msg_id)
            continue

        try:
            lat, lon = float(lat), float(lon)
        except (TypeError, ValueError):
            skip_ids.append(msg_id)
            continue

        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            skip_ids.append(msg_id)
            continue

        cell   = h3.latlng_to_cell(lat, lon, H3_RES)
        geom   = ST_SetSRID(ST_MakePoint(lon, lat), 4326)
        sog    = data.get("sog") or 0.0
        cog    = data.get("cog") or 0.0
        source = data.get("source", "gateway")
        name   = data.get("name") or ""

        pos_values.append({
            "mmsi":        mmsi,
            "name":        name,
            "position":    geom,
            "speed_knots": sog,
            "cog_degrees": cog,
            "h3_index":    cell,
            "data_source": source,
        })

        track_values.append({
            "mmsi":      mmsi,
            "position":  geom,
            "sog":       sog,
            "cog":       cog,
            "timestamp": now,
            "source":    source,
        })

        h3_blob = ujson.dumps({
            "mmsi":       mmsi,
            "name":       name,
            "lat":        lat,
            "lon":        lon,
            "sog":        sog,
            "h3_index":   cell,
            "source":     source,
            "updated_at": now_iso,
        })
        h3_updates.append((cell, mmsi, h3_blob))

        # vessel:{mmsi} hash is read by brain.evaluate_vessel_by_mmsi
        vessel_updates.append((mmsi, {
            "lat":        str(lat),
            "lon":        str(lon),
            "sog":        str(sog),
            "cog":        str(cog),
            "h3_index":   cell,
            "source":     source,
            "updated_at": now_iso,
        }))

        # Track ring-buffer point — consumed by ML kinematic extractor in brain
        ring_pushes.append((mmsi, ujson.dumps({
            "lat": lat, "lon": lon, "sog": sog, "cog": cog, "ts": now_iso,
        })))

        data["h3_index"] = cell
        parsed_data.append((data, cell))
        valid_ids.append(msg_id)

    # ACK malformed/empty messages immediately so they don't block the group
    if skip_ids:
        await r.xack(STREAM_IN, GROUP, *skip_ids)

    if not pos_values:
        return 0

    # --- DB: bulk position upsert (one round-trip) ---------------------------
    pos_stmt = pg_insert(VesselPosition).values(pos_values)
    pos_stmt = pos_stmt.on_conflict_do_update(
        index_elements=["mmsi"],
        set_={
            "position":    pos_stmt.excluded.position,
            "speed_knots": pos_stmt.excluded.speed_knots,
            "cog_degrees": pos_stmt.excluded.cog_degrees,
            "h3_index":    pos_stmt.excluded.h3_index,
            "updated_at":  func.now(),
        },
    )
    await session.execute(pos_stmt)

    # --- DB: bulk track insert (one round-trip) ------------------------------
    await session.execute(pg_insert(VesselTrack).values(track_values))

    await session.commit()

    # --- Ecological enrichment: one HGETALL per unique H3 cell ---------------
    # eco:h3:{cell} is pre-materialised nightly by refresh_ecological_masks.
    # Missing key → no active ecological signal for that cell (safe default).
    unique_eco_cells = list({cell for _, cell in parsed_data})
    eco_by_cell: dict[str, dict] = {}
    if unique_eco_cells:
        async with r.pipeline(transaction=False) as eco_pipe:
            for cell in unique_eco_cells:
                eco_pipe.hgetall(f"eco:h3:{cell}")
            eco_results = await eco_pipe.execute()
        for cell, result in zip(unique_eco_cells, eco_results):
            if result:
                eco_by_cell[cell] = result

    enriched_blobs: list[str] = []
    for data, cell in parsed_data:
        eco = eco_by_cell.get(cell)
        if eco:
            corridors = ujson.loads(eco.get("corridors", "[]"))
            spawning  = ujson.loads(eco.get("spawning",  "[]"))
            data["in_cetacean_corridor"] = bool(corridors)
            data["corridor_species"]     = [s for c in corridors for s in c.get("species", [])]
            data["corridor_season_peak"] = max((c.get("peak", 0.0) for c in corridors), default=0.0)
            data["endangerment_weight"]  = float(eco.get("max_endanger", "0"))
            data["in_spawning_ground"]   = bool(spawning)
            data["spawning_species"]     = [s for g in spawning for s in g.get("species", [])]
            sog = float(data.get("sog") or 0.0)
            data["whale_strike_risk"] = round(
                min(sog / 20.0, 1.0)
                * data["corridor_season_peak"]
                * data["endangerment_weight"],
                4,
            )
        else:
            data["in_cetacean_corridor"] = False
            data["corridor_species"]     = []
            data["corridor_season_peak"] = 0.0
            data["endangerment_weight"]  = 0.0
            data["in_spawning_ground"]   = False
            data["spawning_species"]     = []
            data["whale_strike_risk"]    = 0.0
        enriched_blobs.append(ujson.dumps(data))

    # --- Redis: all ops in a single pipeline (one round-trip) ----------------
    async with r.pipeline(transaction=False) as pipe:
        seen_cells: set[str] = set()
        for cell, mmsi, blob in h3_updates:
            pipe.hset(f"h3:{cell}", mmsi, blob)
            if cell not in seen_cells:
                # Only set TTL once per cell per batch — EXPIRE is idempotent
                pipe.expire(f"h3:{cell}", H3_TTL)
                seen_cells.add(cell)

        for mmsi, vessel_fields in vessel_updates:
            pipe.hset(f"vessel:{mmsi}", mapping=vessel_fields)
            pipe.expire(f"vessel:{mmsi}", H3_TTL)

        # Track ring buffer: RPUSH then LTRIM to keep last TRACK_MAXLEN points.
        # brain/tasks.py does LRANGE 0 TRACK_MAXLEN-1 to feed the ML extractor.
        for mmsi, pt_blob in ring_pushes:
            key = f"vessel:{mmsi}:track"
            pipe.rpush(key, pt_blob)
            pipe.ltrim(key, -TRACK_MAXLEN, -1)
            pipe.expire(key, H3_TTL * 4)   # keep track longer than the hot-state hash

        for blob in enriched_blobs:
            pipe.xadd(STREAM_OUT, {"data": blob}, maxlen=50_000, approximate=True)

        # Batch ACK: XACK supports multiple IDs in one command
        pipe.xack(STREAM_IN, GROUP, *valid_ids)

        await pipe.execute()

    log.debug("spatial_worker.batch_ok count=%d", len(valid_ids))
    return len(valid_ids)


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

        # Fresh session per batch: if a DB error leaves the session in an
        # invalid state, the next batch gets a clean connection from the pool
        # instead of inheriting a broken transaction.
        for _stream_name, messages in results:
            async with _Session() as session:
                try:
                    await _process_batch(r, session, messages)
                except Exception as exc:
                    log.error(
                        "spatial_worker.batch_error count=%d err=%s",
                        len(messages), exc,
                    )

    await pool.aclose()
    await _engine.dispose()
    log.info("spatial_worker.stopped")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
