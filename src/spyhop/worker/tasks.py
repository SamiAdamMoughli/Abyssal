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
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import redis as sync_redis_lib
import ujson
from celery.utils.log import get_task_logger
from geoalchemy2.shape import to_shape
from sqlalchemy import create_engine, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import sessionmaker

from spyhop.config import get_settings
from spyhop.db.models import (
    EnvironmentRaster,
    IUUBlacklist,
    SanctionedVessel,
    VesselPosition,
    VesselTrack,
)
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
    from backend.app.risk_engine import assess, compound_score  # noqa: PLC0415

    t_start = time.monotonic()
    try:
        if settings.DATA_SOURCE == "gfw":
            from backend.app.gfw_vessels import GfwVesselSource  # noqa: PLC0415,E402
            source = GfwVesselSource()
        else:
            from backend.app.sample_data import get_source  # noqa: PLC0415
            source = get_source()
        vessels = source.get_vessels()
        log.info("fetched %d vessels from source", len(vessels))

        # -- Insert current positions as track history ---------------------
        now_utc = datetime.now(timezone.utc)
        track_rows = [
            {
                "mmsi": v.mmsi,
                "lat": v.lat,
                "lon": v.lon,
                "sog": v.speed_knots,
                "cog": v.cog_degrees if v.cog_degrees >= 0 else 0.0,
                "timestamp": now_utc,
                "source": settings.DATA_SOURCE,
            }
            for v in vessels
        ]
        _insert_tracks_sync(track_rows)

        # -- Compute motion profiles + spatial + trajectory from tracks ------
        mmsi_list = [v.mmsi for v in vessels]
        behavior_map = _compute_behaviors_sync(mmsi_list, window_hours=4)
        spatial_map = _compute_spatial_sync(mmsi_list, window_hours=6)
        trajectory_map = _compute_trajectories_sync(
            mmsi_list, window_hours=12
        )
        for v in vessels:
            profile = behavior_map.get(v.mmsi)
            if profile is not None:
                v.behavior = profile.behavior.value
                v.behavior_confidence = profile.confidence
            sf = spatial_map.get(v.mmsi)
            if sf is not None:
                v.nearest_mpa_nm = sf.nearest_mpa_nm
                v.time_in_zone_hours = sf.time_in_zone_hours
                v.border_skirting = sf.border_skirting
            else:
                from backend.app.geo import (  # noqa: PLC0415
                    distance_to_nearest_zone_nm,
                )
                v.nearest_mpa_nm = distance_to_nearest_zone_nm(
                    v.lat, v.lon
                )
            tp = trajectory_map.get(v.mmsi)
            if tp is not None:
                v.trajectory_pattern = tp.pattern.value
                v.trajectory_confidence = tp.confidence

        # -- Spoofing / gap kinematic analysis ----------------------------
        spoofing_map = _compute_spoofing_sync(vessels, window_hours=4)
        for v in vessels:
            sp = spoofing_map.get(v.mmsi)
            if sp is not None:
                v.gap_type = sp.get("gap_type", "")
                v.gap_displacement_nm = sp.get("gap_displacement_nm", -1.0)
                v.spoofing_flag = sp.get("spoofing_flag", False)
                v.spoofing_max_speed_kn = sp.get(
                    "spoofing_max_speed_kn", 0.0
                )

        # -- Contextual fusion: environmental raster ----------------------
        env_map = _compute_environmental_sync(vessels)
        for v in vessels:
            ec = env_map.get(v.mmsi)
            if ec is not None:
                v.sst_celsius = ec.sst_celsius
                v.wave_height_m = ec.wave_height_m
                v.wind_speed_kn = ec.wind_speed_kn
                v.sst_at_thermal_front = ec.sst_at_thermal_front

        # -- Contextual fusion: registry profile cache --------------------
        profile_map = _enrich_vessel_profiles_sync(vessels)
        for v in vessels:
            profile = profile_map.get(v.mmsi)
            if profile:
                v.historical_risk_score = profile.get("historical_risk", -1.0)
                vt = profile.get("verified_type", "")
                if vt:
                    v.verified_vessel_type = vt

        # -- Vessel-to-vessel proximity detection -------------------------
        proximity_map = _detect_proximity_sync(vessels)
        _FISHING_TYPES = {
            "fishing", "trawler", "longliner",
            "purse_seiner", "squid_jigger",
        }
        for v in vessels:
            ir = proximity_map.get(v.mmsi)
            if ir is not None:
                v.rendezvous_partner_type = ir.partner_type
                v.rendezvous_meeting_class = ir.meeting_class.value
                v.rendezvous_duration_hours = max(
                    v.rendezvous_duration_hours, ir.duration_h
                )
                if ir.partner_type.lower() in _FISHING_TYPES:
                    v.nearby_fishing_vessels = max(
                        v.nearby_fishing_vessels, 1
                    )

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
            "behavior_status": v.behavior,
            "behavior_confidence": v.behavior_confidence,
            "cog_degrees": v.cog_degrees,
            "nearest_mpa_nm": v.nearest_mpa_nm,
            "time_in_zone_hours": v.time_in_zone_hours,
            "border_skirting": v.border_skirting,
            "trajectory_pattern": v.trajectory_pattern,
            "trajectory_confidence": v.trajectory_confidence,
            "rendezvous_partner_type": v.rendezvous_partner_type,
            "rendezvous_meeting_class": v.rendezvous_meeting_class,
            "gap_type": v.gap_type,
            "gap_displacement_nm": v.gap_displacement_nm,
            "spoofing_flag": v.spoofing_flag,
            "spoofing_max_speed_kn": v.spoofing_max_speed_kn,
            "sst_celsius": v.sst_celsius,
            "wave_height_m": v.wave_height_m,
            "wind_speed_kn": v.wind_speed_kn,
            "sst_at_thermal_front": v.sst_at_thermal_front,
            "historical_risk_score": v.historical_risk_score,
            "verified_vessel_type": v.verified_vessel_type,
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


def _insert_tracks_sync(rows: list[dict[str, Any]]) -> None:
    """Bulk-insert ping rows into vessel_tracks (no upsert — append-only)."""
    if not rows:
        return
    with SyncSession() as session:
        for row in rows:
            lat = row["lat"]
            lon = row["lon"]
            session.execute(
                pg_insert(VesselTrack).values(
                    mmsi=row["mmsi"],
                    position=func.ST_SetSRID(func.ST_Point(lon, lat), 4326),
                    sog=row["sog"],
                    cog=row.get("cog", 0.0),
                    timestamp=row["timestamp"],
                    source=row.get("source", "unknown"),
                )
            )
        session.commit()


def _compute_behaviors_sync(
    mmsi_list: list[str],
    window_hours: int = 4,
) -> "dict[str, Any]":
    """Query recent tracks and compute motion profiles for a list of MMSIs.

    Returns a dict mapping mmsi → MotionProfile (or absent if < 3 pings).
    """
    from datetime import timezone as tz  # noqa: PLC0415
    from spyhop.analytics.motion_profile import (  # noqa: PLC0415
        MotionPing,
        profile_from_pings,
    )

    cutoff = datetime.now(tz.utc) - timedelta(hours=window_hours)
    result: dict[str, Any] = {}

    if not mmsi_list:
        return result

    with SyncSession() as session:
        rows = session.execute(
            select(VesselTrack)
            .where(
                VesselTrack.mmsi.in_(mmsi_list),
                VesselTrack.timestamp >= cutoff,
            )
            .order_by(VesselTrack.mmsi, VesselTrack.timestamp)
        ).scalars().all()

    # Group pings by MMSI
    buckets: dict[str, list[MotionPing]] = defaultdict(list)
    for row in rows:
        pt = to_shape(row.position)
        buckets[row.mmsi].append(
            MotionPing(
                lat=pt.y,
                lon=pt.x,
                sog=row.sog,
                cog=row.cog,
                ts=row.timestamp,
            )
        )

    for mmsi, pings in buckets.items():
        profile = profile_from_pings(pings)
        if profile is not None:
            result[mmsi] = profile
            log.debug(
                "motion_profile mmsi=%s behavior=%s confidence=%.2f pings=%d",
                mmsi, profile.behavior, profile.confidence, len(pings),
            )

    return result


def _compute_spatial_sync(
    mmsi_list: list[str],
    window_hours: int = 6,
) -> "dict[str, Any]":
    """Compute spatial features (proximity, skirting, time-in-zone) from tracks.

    Reuses the same track query as _compute_behaviors_sync but runs the
    spatial_risk analysis on each vessel's ping window.
    Returns dict mmsi → SpatialFeatures.
    """
    from datetime import timezone as tz  # noqa: PLC0415
    from spyhop.analytics.motion_profile import MotionPing  # noqa: PLC0415
    from spyhop.analytics.spatial_risk import (  # noqa: PLC0415
        compute_spatial_features,
    )

    cutoff = datetime.now(tz.utc) - timedelta(hours=window_hours)
    result: dict[str, Any] = {}

    if not mmsi_list:
        return result

    with SyncSession() as session:
        rows = session.execute(
            select(VesselTrack)
            .where(
                VesselTrack.mmsi.in_(mmsi_list),
                VesselTrack.timestamp >= cutoff,
            )
            .order_by(VesselTrack.mmsi, VesselTrack.timestamp)
        ).scalars().all()

    from collections import defaultdict as _dd  # noqa: PLC0415
    from geoalchemy2.shape import to_shape as _to_shape  # noqa: PLC0415
    buckets: dict[str, list[MotionPing]] = _dd(list)
    for row in rows:
        pt = _to_shape(row.position)
        buckets[row.mmsi].append(
            MotionPing(
                lat=pt.y,
                lon=pt.x,
                sog=row.sog,
                cog=row.cog,
                ts=row.timestamp,
            )
        )

    for mmsi, pings in buckets.items():
        result[mmsi] = compute_spatial_features(pings)

    return result


def _compute_trajectories_sync(
    mmsi_list: list[str],
    window_hours: int = 12,
) -> "dict[str, Any]":
    """Compute trajectory pattern (geometric fingerprint) from 12-hour tracks.

    Returns dict mmsi → TrajectoryProfile.  Absent when < 2h of data.
    Uses a longer window than the motion-profile query to capture full
    trawling grids or holding loops which can span several hours.
    """
    from datetime import timezone as tz  # noqa: PLC0415
    from spyhop.analytics.motion_profile import MotionPing  # noqa: PLC0415
    from spyhop.analytics.trajectory import trajectory_profile  # noqa: PLC0415

    cutoff = datetime.now(tz.utc) - timedelta(hours=window_hours)
    result: dict[str, Any] = {}

    if not mmsi_list:
        return result

    with SyncSession() as session:
        rows = session.execute(
            select(VesselTrack)
            .where(
                VesselTrack.mmsi.in_(mmsi_list),
                VesselTrack.timestamp >= cutoff,
            )
            .order_by(VesselTrack.mmsi, VesselTrack.timestamp)
        ).scalars().all()

    from collections import defaultdict as _dd  # noqa: PLC0415
    from geoalchemy2.shape import to_shape as _ts  # noqa: PLC0415
    buckets: dict[str, list[MotionPing]] = _dd(list)
    for row in rows:
        pt = _ts(row.position)
        buckets[row.mmsi].append(
            MotionPing(
                lat=pt.y, lon=pt.x,
                sog=row.sog, cog=row.cog,
                ts=row.timestamp,
            )
        )

    for mmsi, pings in buckets.items():
        tp = trajectory_profile(pings)
        if tp is not None:
            result[mmsi] = tp
            log.debug(
                "trajectory mmsi=%s pattern=%s confidence=%.2f",
                mmsi, tp.pattern, tp.confidence,
            )

    return result


def _compute_spoofing_sync(
    vessels: "list[Any]",
    window_hours: int = 4,
) -> "dict[str, Any]":
    """Gap kinematic analysis + spoofing signals from track history.

    Two data sources:
      1. vessel_positions (previous cycle) → P_A for gap analysis
      2. vessel_tracks → pings for kinematic violation + static-coord detection

    Returns dict mmsi → dict with keys:
      gap_type, gap_displacement_nm, spoofing_flag, spoofing_max_speed_kn
    """
    from datetime import timezone as tz  # noqa: PLC0415
    from geoalchemy2.shape import to_shape as _ts  # noqa: PLC0415
    from spyhop.analytics.motion_profile import MotionPing  # noqa: PLC0415
    from spyhop.analytics.spoofing import (  # noqa: PLC0415
        analyze_gap,
        analyze_spoofing,
    )

    result: dict[str, Any] = {}
    if not vessels:
        return result

    mmsi_map = {v.mmsi: v for v in vessels}
    mmsi_list = list(mmsi_map.keys())
    now = datetime.now(tz.utc)
    cutoff = now - timedelta(hours=window_hours)

    # --- Fetch previous vessel_positions (P_A for gap analysis) -------------
    prev_positions: dict[str, Any] = {}
    with SyncSession() as session:
        from spyhop.db.models import VesselPosition  # noqa: PLC0415
        rows = session.execute(
            select(VesselPosition).where(
                VesselPosition.mmsi.in_(mmsi_list)
            )
        ).scalars().all()
        for row in rows:
            prev_positions[row.mmsi] = {
                "lat": row.lat,
                "lon": row.lon,
                "updated_at": row.updated_at,
                "speed_knots": row.speed_knots,
            }

    # --- Fetch vessel_tracks for kinematic / static analysis ----------------
    with SyncSession() as session:
        track_rows = session.execute(
            select(VesselTrack)
            .where(
                VesselTrack.mmsi.in_(mmsi_list),
                VesselTrack.timestamp >= cutoff,
            )
            .order_by(VesselTrack.mmsi, VesselTrack.timestamp)
        ).scalars().all()

    from collections import defaultdict as _dd  # noqa: PLC0415
    buckets: dict[str, list[MotionPing]] = _dd(list)
    for row in track_rows:
        pt = _ts(row.position)
        buckets[row.mmsi].append(
            MotionPing(lat=pt.y, lon=pt.x, sog=row.sog,
                       cog=row.cog, ts=row.timestamp)
        )

    for mmsi, v in mmsi_map.items():
        entry: dict[str, Any] = {
            "gap_type": "",
            "gap_displacement_nm": -1.0,
            "spoofing_flag": False,
            "spoofing_max_speed_kn": 0.0,
        }

        # Gap kinematic analysis (needs P_A and P_B)
        prev = prev_positions.get(mmsi)
        if prev and v.ais_gap_hours >= 2.0:
            pa_ts = prev.get("updated_at")
            if pa_ts is not None:
                ga = analyze_gap(
                    last_lat=prev["lat"],
                    last_lon=prev["lon"],
                    last_ts=pa_ts,
                    current_lat=v.lat,
                    current_lon=v.lon,
                    current_ts=now,
                    cruise_speed_kn=prev.get("speed_knots", -1.0),
                )
                if ga is not None:
                    entry["gap_type"] = ga.gap_type.value
                    entry["gap_displacement_nm"] = ga.displacement_nm

        # Spoofing analysis from track pings
        pings = buckets.get(mmsi, [])
        if pings:
            sa_result = analyze_spoofing(pings)
            if sa_result is not None:
                entry["spoofing_flag"] = sa_result.is_suspicious
                entry["spoofing_max_speed_kn"] = (
                    sa_result.kinematic.max_implied_speed_kn
                )
                log.debug(
                    "spoofing mmsi=%s flag=%s max_kn=%.0f static=%.2f",
                    mmsi,
                    sa_result.is_suspicious,
                    sa_result.kinematic.max_implied_speed_kn,
                    sa_result.static_coords.static_fraction,
                )

        if (
            entry["gap_type"]
            or entry["spoofing_flag"]
            or entry["gap_displacement_nm"] >= 0
        ):
            result[mmsi] = entry

    return result


def _detect_proximity_sync(
    vessels: "list[Any]",
    proximity_nm: float = 0.3,
) -> "dict[str, Any]":
    """In-memory pairwise proximity detection with Redis duration tracking.

    Finds vessel pairs that are slow-moving and within ``proximity_nm``
    of each other, classifies the encounter type, and maintains a Redis
    state machine to track how long the proximity has lasted.

    Returns dict mmsi → InteractionResult for vessels with active encounters.
    The caller should update Vessel.nearby_fishing_vessels,
    rendezvous_duration_hours, rendezvous_partner_type, and
    rendezvous_meeting_class from the returned dict.
    """
    import math
    import ujson
    from datetime import timezone as tz
    from spyhop.analytics.interaction import (  # noqa: PLC0415
        InteractionResult,
        MeetingClass,
        classify_pair,
    )

    NM_PER_DEG_LAT = 60.0
    REDIS_TTL = 720          # 12 minutes — survives two missed cycles
    MAX_SOG_KN = 3.0

    now = datetime.now(tz.utc)
    result: dict[str, Any] = {}

    if len(vessels) < 2:
        return result

    # Build a compact array for the O(N²) scan
    pts = []
    for v in vessels:
        if v.speed_knots < MAX_SOG_KN:
            pts.append(v)

    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            a, b = pts[i], pts[j]
            dlat = a.lat - b.lat
            mid_lat = math.radians((a.lat + b.lat) / 2)
            dlon = (a.lon - b.lon) * math.cos(mid_lat)
            dist_nm = math.sqrt(dlat ** 2 + dlon ** 2) * NM_PER_DEG_LAT
            if dist_nm > proximity_nm:
                continue

            lo, hi = sorted([a.mmsi, b.mmsi])
            key = f"iv:{lo}:{hi}"
            raw = _sync_redis.get(key)
            if raw:
                state = ujson.loads(raw)
                first_ts = datetime.fromisoformat(state["first_ts"])
                duration_h = (now - first_ts).total_seconds() / 3600.0
            else:
                state = {
                    "first_ts": now.isoformat(),
                    "type_a": a.vessel_type,
                    "type_b": b.vessel_type,
                }
                duration_h = 0.0

            _sync_redis.setex(key, REDIS_TTL, ujson.dumps(state))

            mc = classify_pair(
                state.get("type_a", a.vessel_type),
                state.get("type_b", b.vessel_type),
            )

            for vessel, partner in ((a, b), (b, a)):
                result[vessel.mmsi] = InteractionResult(
                    partner_mmsi=partner.mmsi,
                    partner_type=(partner.vessel_type or ""),
                    meeting_class=mc,
                    duration_h=duration_h,
                    dist_nm=dist_nm,
                )
                log.debug(
                    "proximity mmsi=%s partner=%s class=%s "
                    "dist=%.2fnm dur=%.1fh",
                    vessel.mmsi, partner.mmsi, mc.value,
                    dist_nm, duration_h,
                )

    return result


@celery_app.task(
    name="spyhop.worker.tasks.prune_vessel_tracks",
    soft_time_limit=60,
    time_limit=90,
)
def prune_vessel_tracks() -> dict[str, Any]:
    """Delete vessel_tracks rows older than 7 days (keep DB lean)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    with SyncSession() as session:
        result = session.execute(
            text(
                "DELETE FROM vessel_tracks WHERE timestamp < :cutoff"
            ),
            {"cutoff": cutoff},
        )
        session.commit()
        deleted = result.rowcount
    log.info("prune_vessel_tracks deleted=%d", deleted)
    return {"status": "ok", "deleted": deleted}


# ---------------------------------------------------------------------------
# Task: sync_environment_raster
# ---------------------------------------------------------------------------


@celery_app.task(
    name="spyhop.worker.tasks.sync_environment_raster",
    soft_time_limit=300,
    time_limit=360,
)
def sync_environment_raster() -> dict[str, Any]:
    """Download and upsert the latest environmental raster grid.

    In production this would call CMEMS / NOAA ERDDAP REST APIs to fetch the
    most recent SST and WAV analysis products (GRIB/NetCDF), re-grid to 0.25°,
    and upsert into environment_raster.

    For the current deployment the task generates a synthetic-but-realistic
    global grid (4° step to keep the table small) with:
      - SST: realistic tropical/mid-lat gradient (8-30°C)
      - Wave height: Hs correlated with latitude and season
      - Wind speed: 5-35 kn, higher in westerly storm belts
    The real API integration is a drop-in replacement for the generation block.
    """
    import math as _math  # noqa: PLC0415
    import random as _rnd  # noqa: PLC0415
    from geoalchemy2.shape import from_shape  # noqa: PLC0415
    from shapely.geometry import Point  # noqa: PLC0415

    now = datetime.now(timezone.utc)
    _rnd.seed(int(now.timestamp() / 3600))  # stable within each hour

    rows = []
    # 4° grid → ~3 600 cells globally (manageable, updates in seconds)
    for lat in range(-88, 90, 4):
        for lon in range(-180, 180, 4):
            lat_f = float(lat)
            lon_f = float(lon)

            # SST: decreases toward poles, slight diurnal noise
            sst = 28.0 - abs(lat_f) * 0.42 + _rnd.gauss(0, 1.2)
            sst = max(-1.8, min(32.0, sst))

            # Wave height: higher in storm belts (40-60° lat)
            lat_abs = abs(lat_f)
            base_wave = 0.5 + (lat_abs / 60) ** 2 * 4.0
            wave = base_wave + _rnd.gauss(0, 0.4)
            wave = max(0.1, min(12.0, wave))

            # Wind: correlated with wave, converted kn
            wind_ms = wave * 2.5 + _rnd.gauss(0, 2.0)
            wind_kn = max(0.0, wind_ms * 1.944)

            rows.append({
                "position": from_shape(Point(lon_f, lat_f), srid=4326),
                "sst_celsius": round(sst, 2),
                "wave_height_m": round(wave, 2),
                "wind_speed_kn": round(wind_kn, 1),
                "valid_time": now,
            })

    with SyncSession() as session:
        # Full replace: delete old rows, bulk insert new ones
        session.execute(text("DELETE FROM environment_raster"))
        session.bulk_insert_mappings(EnvironmentRaster, rows)
        session.commit()

    log.info("sync_environment_raster cells=%d", len(rows))
    return {"status": "ok", "cells": len(rows)}


# ---------------------------------------------------------------------------
# Context fusion helpers  (environmental + registry)
# ---------------------------------------------------------------------------


def _compute_environmental_sync(
    vessels: "list[Any]",
    search_radius_deg: float = 2.0,
) -> "dict[str, Any]":
    """Nearest-neighbour PostGIS lookup of environmental raster for each vessel.

    Uses ST_DWithin on the GiST index to find the closest raster cell within
    ``search_radius_deg`` degrees (~220 km at equator). For each vessel:
      - SST, wave height, wind speed from the nearest cell
      - SST thermal front detected by checking if any of the 8 neighbours
        differ from the vessel cell by >= 2°C

    Returns dict mmsi → EnvironmentalContext dataclass.
    """
    from spyhop.analytics.context_fusion import (  # noqa: PLC0415
        EnvironmentalContext, detect_sst_front,
        SST_NO_DATA, WAVE_NO_DATA, WIND_NO_DATA,
    )

    if not vessels:
        return {}

    result: dict[str, Any] = {}
    with SyncSession() as session:
        for v in vessels:
            # Nearest raster cell (ORDER BY distance ASC LIMIT 1)
            row = session.execute(
                text(
                    """
                    SELECT sst_celsius, wave_height_m, wind_speed_kn
                    FROM environment_raster
                    WHERE ST_DWithin(
                        position::geography,
                        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                        :radius_m
                    )
                    ORDER BY position <-> ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)
                    LIMIT 1
                    """
                ),
                {
                    "lat": v.lat, "lon": v.lon,
                    "radius_m": search_radius_deg * 111_000,
                },
            ).fetchone()

            if row is None:
                continue

            sst = float(row.sst_celsius)
            wave = float(row.wave_height_m)
            wind = float(row.wind_speed_kn)

            # Nearby cells for gradient detection (8-neighbour, 2° apart)
            neighbour_rows = session.execute(
                text(
                    """
                    SELECT sst_celsius FROM environment_raster
                    WHERE ST_DWithin(
                        position::geography,
                        ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography,
                        :radius_m
                    )
                    AND sst_celsius > -999
                    LIMIT 16
                    """
                ),
                {
                    "lat": v.lat, "lon": v.lon,
                    "radius_m": 3 * 111_000,  # ~3° radius for neighbours
                },
            ).fetchall()

            nearby_sst = [float(r.sst_celsius) for r in neighbour_rows]
            at_front = detect_sst_front(sst, nearby_sst)

            result[v.mmsi] = EnvironmentalContext(
                sst_celsius=sst,
                wave_height_m=wave,
                wind_speed_kn=wind,
                sst_at_thermal_front=at_front,
            )

    return result


def _enrich_vessel_profiles_sync(
    vessels: "list[Any]",
) -> "dict[str, Any]":
    """Redis MMSI profile cache + historical risk from vessel_positions.

    Cache key: ``vessel_profile:{mmsi}``   TTL: 30 days.
    Value: JSON with keys  verified_type, home_port, historical_risk.

    On cache miss: queries vessel_positions for the vessel's current risk_score
    (which is the previous cycle's score — the record hasn't been upserted yet
    for the current cycle). Also looks up verified_vessel_type if stored.

    On cache hit: deserializes and returns the cached profile directly.

    The verified_type field is currently populated from the DB record itself
    (previous upsert) rather than a live registry call. A production integration
    would call IHS Markit / Equasis here on first-ever sighting.
    """
    if not vessels:
        return {}

    redis_client = sync_redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    result: dict[str, Any] = {}
    miss_mmsi: list[str] = []

    for v in vessels:
        key = f"vessel_profile:{v.mmsi}"
        cached = redis_client.get(key)
        if cached:
            profile = ujson.loads(cached)
            result[v.mmsi] = profile
        else:
            miss_mmsi.append(v.mmsi)

    if miss_mmsi:
        with SyncSession() as session:
            rows = session.execute(
                select(
                    VesselPosition.mmsi,
                    VesselPosition.risk_score,
                    VesselPosition.verified_vessel_type,
                ).where(VesselPosition.mmsi.in_(miss_mmsi))
            ).all()

        for row in rows:
            profile = {
                "verified_type": row.verified_vessel_type or "",
                "home_port": "",          # placeholder for registry integration
                "historical_risk": float(row.risk_score),
            }
            key = f"vessel_profile:{row.mmsi}"
            redis_client.setex(
                key,
                86400 * 30,   # 30-day TTL (PROFILE_CACHE_TTL_S)
                ujson.dumps(profile),
            )
            result[row.mmsi] = profile
            log.debug(
                "vessel_profile cache miss mmsi=%s hist_risk=%.0f",
                row.mmsi, profile["historical_risk"],
            )

    return result
