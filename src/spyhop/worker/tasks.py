"""Celery task implementations.

All tasks are SYNCHRONOUS (Celery default). DB access uses the psycopg2 sync
driver; Redis access uses redis-py sync client. This avoids the complexity of
running an asyncio event loop inside a Celery worker process.

Performance contract:
  - fetch_and_score_vessels: Redis sorted-set updates are batched via a single
    pipeline — O(N) DB upserts + 1 Redis pipeline RTT + 1 publish call.
  - IUU / sanctions syncs: bulk truncate-and-insert to keep the operation
    idempotent and safe for concurrent beat invocations.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import redis as sync_redis_lib
import ujson
from celery.utils.log import get_task_logger
from sqlalchemy import create_engine, delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from spyhop.config import get_settings
from spyhop.db.models import IUUBlacklist, SanctionedVessel, VesselPosition
from spyhop.worker.celery_app import celery_app

log = get_task_logger(__name__)
settings = get_settings()

# ---------------------------------------------------------------------------
# Synchronous DB engine (psycopg2 — Celery-safe, no asyncio required)
# ---------------------------------------------------------------------------

_sync_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=False,
)
SyncSession = sessionmaker(
    bind=_sync_engine,
    autocommit=False,
    autoflush=False,
)

# ---------------------------------------------------------------------------
# Synchronous Redis client (redis-py — Celery-safe)
# ---------------------------------------------------------------------------

_sync_redis = sync_redis_lib.from_url(
    settings.REDIS_URL,  # DB 0 — data plane (scores, pubsub)
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True,
)

VESSEL_SCORES_KEY = "vessel:scores"
VESSEL_UPDATES_CHANNEL = "vessel:updates"


# ---------------------------------------------------------------------------
# Task: fetch_and_score_vessels
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.tasks.fetch_and_score_vessels",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=240,
    time_limit=300,
)
def fetch_and_score_vessels(self: Any) -> dict[str, Any]:
    """Fetch vessels, score them, persist to PostGIS, push to Redis.

    Pipeline strategy (minimises network RTTs):
      1. Fetch all vessels from the configured source.
      2. Run risk engine on each vessel (compound_score).
      3. Upsert all rows to PostGIS in a single transaction.
      4. Batch-update Redis sorted set via pipeline (1 RTT for N vessels).
      5. Publish a single JSON payload to the vessel:updates channel.
    """
    # Lazy import: backend.app lives alongside src/ on PYTHONPATH.
    # Deferring avoids problems if the worker boots before the app package
    # is fully resolved (e.g., during Celery worker startup scanning).
    from backend.app.risk_engine import assess, compound_score  # noqa: PLC0415
    from backend.app.sample_data import get_source  # noqa: PLC0415

    t_start = time.monotonic()
    try:
        source = get_source()
        vessels = source.get_vessels()
        log.info("fetched %d vessels from source", len(vessels))

        # -- Risk engine scoring -------------------------------------------
        assessments = []
        for v in vessels:
            try:
                ta = compound_score(v)
            except Exception as exc:  # noqa: BLE001
                log.warning("scoring_error mmsi=%s err=%s", v.mmsi, exc)
                ta = assess(v)
            assessments.append(ta)

        # -- PostGIS upsert (sync, single transaction) ---------------------
        vessel_rows = _build_vessel_rows(assessments)
        _upsert_vessels_sync(vessel_rows)

        # -- Redis: pipeline sorted-set updates (ONE RTT) ------------------
        score_map = {a.vessel.mmsi: a.score for a in assessments}
        _pipeline_update_scores(score_map)

        # -- Redis: publish batch update payload ---------------------------
        update_payload = [
            {
                "mmsi": a.vessel.mmsi,
                "name": a.vessel.name,
                "lat": a.vessel.lat,
                "lon": a.vessel.lon,
                "score": a.score,
                "top_reason": (
                    a.top_reason.label if a.top_reason else None
                ),
                "vessel_type": a.vessel.vessel_type,
            }
            for a in assessments
        ]
        _sync_redis.publish(
            VESSEL_UPDATES_CHANNEL, ujson.dumps(update_payload)
        )

        elapsed_ms = (time.monotonic() - t_start) * 1000
        log.info(
            "fetch_and_score complete vessels=%d elapsed_ms=%.1f",
            len(assessments), elapsed_ms,
        )
        return {
            "status": "ok",
            "vessels": len(assessments),
            "elapsed_ms": round(elapsed_ms, 1),
        }

    except Exception as exc:
        log.exception("fetch_and_score_vessels failed: %s", exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: sync_iuu_list
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.tasks.sync_iuu_list",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=120,
    time_limit=180,
)
def sync_iuu_list(self: Any) -> dict[str, Any]:
    """Refresh the IUU blacklist in PostgreSQL + rebuild in-memory index."""
    from backend.app.sources.iuu_list import (  # noqa: PLC0415
        fetch_iuu,
        refresh,
    )
    try:
        entries = fetch_iuu()
        _replace_iuu_blacklist_sync(entries)
        refresh()  # also refresh the in-memory JSON cache
        log.info("sync_iuu_list complete entries=%d", len(entries))
        return {"status": "ok", "entries": len(entries)}
    except Exception as exc:
        log.exception("sync_iuu_list failed: %s", exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Task: sync_sanctions
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.tasks.sync_sanctions",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    soft_time_limit=600,
    time_limit=700,
)
def sync_sanctions(self: Any) -> dict[str, Any]:
    """Stream OpenSanctions bulk feed and persist vessel entities to PG."""
    from backend.app.sources.opensanctions import (  # noqa: PLC0415
        fetch_sanctioned_vessels,
        refresh,
    )
    try:
        entries = fetch_sanctioned_vessels()
        _replace_sanctioned_vessels_sync(entries)
        refresh()  # also refresh in-memory JSON cache
        log.info("sync_sanctions complete entries=%d", len(entries))
        return {"status": "ok", "entries": len(entries)}
    except Exception as exc:
        log.exception("sync_sanctions failed: %s", exc)
        raise self.retry(exc=exc)


# ---------------------------------------------------------------------------
# Private sync helpers (not Celery tasks)
# ---------------------------------------------------------------------------

def _build_vessel_rows(assessments: list) -> list[dict[str, Any]]:
    """Serialise TargetAssessment list into dicts ready for DB upsert."""
    rows = []
    for ta in assessments:
        v = ta.vessel
        reasons = [
            {
                "points": r.points,
                "label": r.label,
                "detail": r.detail,
                "evidence_type": r.evidence_type,
            }
            for r in ta.reasons
        ]
        rows.append({
            "mmsi": v.mmsi,
            "name": v.name,
            "lat": v.lat,
            "lon": v.lon,
            "speed_knots": v.speed_knots,
            "flag": v.flag,
            "vessel_type": v.vessel_type,
            "ais_gap_hours": v.ais_gap_hours,
            "loitering_hours": v.loitering_hours,
            "in_protected_area": v.in_protected_area,
            "recent_port_calls": v.recent_port_calls,
            "days_since_port": v.days_since_port,
            "distance_to_nearest_port_nm": (
                v.distance_to_nearest_port_nm
            ),
            "nearby_fishing_vessels": v.nearby_fishing_vessels,
            "rendezvous_duration_hours": v.rendezvous_duration_hours,
            "ais_vessel_class": v.ais_vessel_class,
            "risk_score": ta.score,
            "top_reason_label": (
                ta.top_reason.label if ta.top_reason else None
            ),
            "reasons_json": reasons,
            "data_source": settings.DATA_SOURCE,
        })
    return rows


def _upsert_vessels_sync(rows: list[dict[str, Any]]) -> None:
    """Bulk-upsert vessel rows using sync SQLAlchemy + psycopg2."""
    with SyncSession() as session:
        for row in rows:
            lat = row.pop("lat")
            lon = row.pop("lon")
            stmt = pg_insert(VesselPosition).values(
                position=func.ST_SetSRID(
                    func.ST_Point(lon, lat), 4326
                ),
                **row,
            )
            update_dict = {
                col.name: stmt.excluded[col.name]
                for col in VesselPosition.__table__.columns
                if col.name not in ("id", "mmsi", "created_at")
            }
            stmt = stmt.on_conflict_do_update(
                constraint="uq_vessel_positions_mmsi",
                set_=update_dict,
            )
            session.execute(stmt)
        session.commit()


def _pipeline_update_scores(score_map: dict[str, float]) -> None:
    """Batch-update Redis sorted set in a single pipeline (1 RTT).

    A Redis pipeline buffers all commands client-side and flushes them as
    one batch — reducing network roundtrips from O(N) to O(1).
    """
    if not score_map:
        return
    pipe = _sync_redis.pipeline(transaction=False)
    for mmsi, score in score_map.items():
        pipe.zadd(VESSEL_SCORES_KEY, {mmsi: score})
    pipe.execute()
    log.debug("pipeline_update_scores updated=%d", len(score_map))


def _replace_iuu_blacklist_sync(entries: list[dict[str, Any]]) -> None:
    """Truncate + re-insert IUU blacklist records atomically."""
    with SyncSession() as session:
        session.execute(delete(IUUBlacklist))
        now = datetime.now(timezone.utc)
        for e in entries:
            session.add(IUUBlacklist(
                listing_source=e.get("source", "CCAMLR"),
                mmsi=e.get("mmsi"),
                imo=e.get("imo"),
                vessel_name=e.get("name"),
                aliases_json=e.get("aliases", []),
                flag=e.get("flag"),
                listing_year=e.get("year"),
                raw_json=e,
                synced_at=now,
            ))
        session.commit()


def _replace_sanctioned_vessels_sync(
    entries: list[dict[str, Any]],
) -> None:
    """Truncate + re-insert sanctioned vessel records atomically."""
    with SyncSession() as session:
        session.execute(delete(SanctionedVessel))
        now = datetime.now(timezone.utc)
        for e in entries:
            session.add(SanctionedVessel(
                opensanctions_id=e["id"],
                vessel_name=e.get("name"),
                aliases_json=e.get("aliases", []),
                mmsi=e.get("mmsi"),
                imo=e.get("imo"),
                flag=e.get("flag"),
                sanctions_datasets=e.get("sanctions", []),
                source_url=e.get("source_url"),
                synced_at=now,
            ))
        session.commit()
