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
    GFWVesselRegistry,
    H3RiskCorridor,
    IUUBlacklist,
    SanctionedVessel,
    VesselPosition,
    VesselPositionSnapshot,
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
# Task: fetch_gfw_vessels  (replaces the broken fetch_and_score_vessels)
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.tasks.fetch_gfw_vessels",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=180,
    time_limit=240,
)
def fetch_gfw_vessels(self: Any) -> dict[str, Any]:
    """Fetch recent vessel positions from GFW and upsert to PostGIS + Redis.

    Calls /v3/events for the last 24 h, deduplicates by MMSI, applies a simple
    rule-based risk score from vesselx.brain.rules, and persists results.
    """
    import time as _time

    from vesselx.brain.rules import RULES, Severity

    t0 = _time.monotonic()

    try:
        from spyhop.sources.gfw import fetch_recent_vessels
        vessels = fetch_recent_vessels(hours=168, limit=2000)  # 7 days — GFW has multi-day latency
    except Exception as exc:
        log.exception("fetch_gfw_vessels: GFW API call failed: %s", exc)
        raise self.retry(exc=exc)

    if not vessels:
        log.warning("fetch_gfw_vessels: no vessels returned from GFW")
        return {"status": "ok", "vessels": 0}

    log.info("fetch_gfw_vessels: fetched %d vessels from GFW", len(vessels))

    from spyhop.enrichment.mpa import in_protected_area as _in_mpa

    # Build IUU MMSI lookup from DB (fast set lookup per vessel)
    _iuu_mmsis: set[str] = set()
    _iuu_names: set[str] = set()
    with SyncSession() as _s:
        from sqlalchemy import select as _sel  # noqa: PLC0415
        from spyhop.db.models import IUUBlacklist  # noqa: PLC0415
        for row in _s.execute(_sel(IUUBlacklist.mmsi, IUUBlacklist.vessel_name)).all():
            if row.mmsi: _iuu_mmsis.add(row.mmsi.strip())
            if row.vessel_name: _iuu_names.add(row.vessel_name.strip().upper())

    log.info("fetch_gfw_vessels: iuu_mmsis=%d iuu_names=%d", len(_iuu_mmsis), len(_iuu_names))

    now_utc = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    score_map: dict[str, float] = {}

    for v in vessels:
        mmsi = v["mmsi"]
        lat, lon = v["lat"], v["lon"]
        name_upper = (v.get("name") or "").strip().upper()
        et = v.get("_ev_type", "")

        on_iuu = mmsi in _iuu_mmsis or (bool(name_upper) and name_upper in _iuu_names)
        mpa_name = _in_mpa(lat, lon)
        flag = v.get("flag", "UNK")

        state = {
            "mmsi":            mmsi,
            "lat":             lat,
            "lon":             lon,
            "flag":            flag,
            "vessel_type":     v.get("vessel_type", "fishing"),
            "speed_knots":     v.get("speed_knots", 0.0),
            "sog":             v.get("speed_knots", 0.0),
            "ais_gap_hours":   6.0 if et == "gap" else 0.0,
            "loitering_hours": 2.0 if et == "loitering" else 0.0,
            "in_protected_area": bool(mpa_name),
            "behavior_status": "loitering" if et == "loitering" else "unknown",
            "behavior_confidence": 0.8,
            "border_skirting": False,
            "rendezvous_duration_hours": 1.0 if et == "encounter" else 0.0,
            "on_iuu_blacklist": on_iuu,
            "time_in_zone_hours": 5.0 if bool(mpa_name) and et == "loitering" else 0.0,
        }

        weights = {
            Severity.CRITICAL: 40,
            Severity.ALERT:    25,
            Severity.WARNING:  15,
            Severity.INFO:      5,
        }
        raw_score = 0.0
        triggered: list[dict] = []
        for rule in RULES:
            try:
                if rule.predicate(state):
                    raw_score += weights.get(rule.severity, 5)
                    triggered.append({
                        "points": weights.get(rule.severity, 5),
                        "label":  rule.label,
                        "detail": rule.message(state),
                        "evidence_type": rule.id,
                    })
            except Exception:
                pass

        risk_score = min(raw_score, 100.0)
        top_reason = triggered[0]["label"] if triggered else None

        rows.append({
            "mmsi":                      v["mmsi"],
            "name":                      v.get("name", ""),
            "flag":                      v.get("flag", "UNK"),
            "vessel_type":               v.get("vessel_type", "fishing"),
            "lat":                       v["lat"],
            "lon":                       v["lon"],
            "speed_knots":               v.get("speed_knots", 0.0),
            "ais_gap_hours":             state["ais_gap_hours"],
            "loitering_hours":           state["loitering_hours"],
            "in_protected_area":         bool(mpa_name),
            "recent_port_calls":         -1,
            "days_since_port":           -1.0,
            "distance_to_nearest_port_nm": -1.0,
            "nearby_fishing_vessels":    0,
            "rendezvous_duration_hours": state["rendezvous_duration_hours"],
            "ais_vessel_class":          "",
            "behavior_status":           state["behavior_status"],
            "behavior_confidence":       state["behavior_confidence"],
            "cog_degrees":               -1.0,
            "nearest_mpa_nm":            -1.0,
            "risk_score":                risk_score,
            "top_reason_label":          top_reason,
            "reasons_json":              triggered[:5],
            "data_source":               "gfw",
        })
        score_map[v["mmsi"]] = risk_score

    rows = _enrich_with_gfw(rows)
    _upsert_vessels_sync(rows)
    _pipeline_update_scores(score_map)

    update_payload = [
        {
            "mmsi":        r["mmsi"],
            "name":        r["name"],
            "lat":         r["lat"],
            "lon":         r["lon"],
            "score":       r["risk_score"],
            "top_reason":  r["top_reason_label"],
            "vessel_type": r["vessel_type"],
        }
        for r in rows
    ]
    _sync_redis.publish(VESSEL_UPDATES_CHANNEL, ujson.dumps(update_payload))

    elapsed_ms = (_time.monotonic() - t0) * 1000
    log.info(
        "fetch_gfw_vessels complete vessels=%d elapsed_ms=%.1f",
        len(rows), elapsed_ms,
    )
    return {"status": "ok", "vessels": len(rows), "elapsed_ms": round(elapsed_ms, 1)}


# ---------------------------------------------------------------------------
# Task: fetch_digitraffic_vessels  — all vessel types via Finland Digitraffic
# ---------------------------------------------------------------------------

@celery_app.task(
    name="spyhop.worker.tasks.fetch_digitraffic_vessels",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=120,
    time_limit=150,
)
def fetch_digitraffic_vessels(self: Any) -> dict[str, Any]:
    """Fetch live AIS positions from Digitraffic (Finnish waters + Baltic Sea).

    Joins /v1/vessels (name, ship type) with /v1/locations (lat/lon/sog) and
    upserts into vessel_positions.  Covers ALL vessel types — cargo, tanker,
    passenger, fishing, special — at no API cost under CC BY 4.0.
    """
    import gzip as _gzip
    import time as _time
    from urllib.request import urlopen as _urlopen, Request as _Request

    from spyhop.enrichment.mpa import in_protected_area as _in_mpa

    _HEADERS = {"Accept": "application/json", "Accept-Encoding": "gzip"}
    _BASE = "https://meri.digitraffic.fi/api/ais/v1"

    # AIS numeric ship type → our vessel_type label
    _SHIP_TYPE: list[tuple[range, str]] = [
        (range(30, 40), "fishing"),
        (range(60, 70), "passenger"),
        (range(70, 80), "cargo"),
        (range(80, 90), "tanker"),
        (range(50, 60), "special"),
        (range(90, 100), "other"),
    ]

    def _ship_label(t: int | None) -> str:
        if t is None:
            return "unknown"
        for r, label in _SHIP_TYPE:
            if t in r:
                return label
        return "unknown"

    def _fetch(path: str) -> Any:
        req = _Request(f"{_BASE}{path}", headers=_HEADERS)
        with _urlopen(req, timeout=30) as resp:
            raw = resp.read()
        try:
            return ujson.loads(_gzip.decompress(raw))
        except Exception:
            return ujson.loads(raw)

    t0 = _time.monotonic()
    try:
        vessel_meta = _fetch("/vessels")   # list of {mmsi, name, shipType, ...}
        pos_fc      = _fetch("/locations") # GeoJSON FeatureCollection
    except Exception as exc:
        log.exception("fetch_digitraffic_vessels: HTTP error: %s", exc)
        raise self.retry(exc=exc)

    # Index positions by MMSI
    positions: dict[str, dict] = {}
    for feat in pos_fc.get("features", []):
        mmsi = str(feat.get("mmsi", ""))
        coords = (feat.get("geometry") or {}).get("coordinates")
        if not mmsi or not coords:
            continue
        props = feat.get("properties") or {}
        positions[mmsi] = {"lon": coords[0], "lat": coords[1], "sog": props.get("sog") or 0.0}

    rows: list[dict[str, Any]] = []
    score_map: dict[str, float] = {}

    for v in vessel_meta:
        mmsi = str(v.get("mmsi", ""))
        pos = positions.get(mmsi)
        if not pos:
            continue
        lat, lon, sog = pos["lat"], pos["lon"], pos["sog"]
        if not (-90 < lat < 90) or not (-180 < lon < 180):
            continue

        vtype = _ship_label(v.get("shipType"))
        name = (v.get("name") or f"V-{mmsi[-4:]}").strip()[:200]
        mpa_name = _in_mpa(lat, lon)
        in_mpa = mpa_name is not None

        raw_score = 0.0
        top_reason = None
        if in_mpa:
            raw_score += 40.0
            top_reason = f"In MPA: {mpa_name}"

        risk_score = min(raw_score, 100.0)
        score_map[mmsi] = risk_score

        rows.append({
            "mmsi":                      mmsi,
            "name":                      name,
            "flag":                      "UNK",
            "vessel_type":               vtype,
            "lat":                       lat,
            "lon":                       lon,
            "speed_knots":               sog,
            "ais_gap_hours":             0.0,
            "loitering_hours":           0.0,
            "rendezvous_duration_hours": 0.0,
            "in_protected_area":         in_mpa,
            "risk_score":                risk_score,
            "top_reason_label":          top_reason,
            "reasons_json":              [],
            "data_source":               "digitraffic",
        })

    rows = _enrich_with_gfw(rows)
    _upsert_vessels_sync(rows)
    _pipeline_update_scores(score_map)

    elapsed_ms = (_time.monotonic() - t0) * 1000
    log.info("fetch_digitraffic_vessels complete vessels=%d elapsed_ms=%.1f", len(rows), elapsed_ms)
    return {"status": "ok", "vessels": len(rows), "elapsed_ms": round(elapsed_ms, 1)}


# ---------------------------------------------------------------------------
# Task: fetch_and_score_vessels  (legacy — kept for reference, not scheduled)
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
            from backend.app.gfw_vessels import (  # noqa: PLC0415
                GfwVesselSource,
            )
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
        vessel_rows = _enrich_with_gfw(vessel_rows)
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
# Task: load_gfw_vessel_registry  (one-shot manual trigger)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="spyhop.worker.tasks.load_gfw_vessel_registry",
    soft_time_limit=600,
    time_limit=720,
)
def load_gfw_vessel_registry(csv_path: str | None = None) -> dict[str, Any]:
    """Bulk-load fishing-vessels-v3.csv into gfw_vessel_registry.

    Uses PostgreSQL COPY via psycopg2 for throughput (~773k rows in <30s).
    Idempotent: truncates the table and refreshes the materialized view on
    every run. Trigger manually via:
        celery -A spyhop.worker.celery_app call \\
            spyhop.worker.tasks.load_gfw_vessel_registry
    """
    import csv
    import io
    import os

    path = csv_path or os.path.join(
        os.path.dirname(__file__), "../../../data/fishing-vessels-v3.csv"
    )
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"GFW registry CSV not found: {path}")

    def _parse_float(v: str) -> float | None:
        v = v.strip()
        return float(v) if v else None

    def _parse_bool(v: str) -> bool | None:
        v = v.strip().lower()
        if v == "true":
            return True
        if v == "false":
            return False
        return None

    def _parse_str(v: str) -> str | None:
        v = v.strip()
        return v if v else None

    col_order = [
        "mmsi", "year", "flag_ais", "flag_registry", "flag_gfw",
        "vessel_class_inferred", "vessel_class_inferred_score",
        "vessel_class_registry", "vessel_class_gfw",
        "self_reported_fishing_vessel",
        "length_m_gfw", "engine_power_kw_gfw", "tonnage_gt_gfw",
        "registries_listed", "active_hours", "fishing_hours",
    ]

    log.info("gfw_registry.load.start", path=path)
    t0 = time.time()

    with SyncSession() as session:
        conn = session.connection().connection  # raw psycopg2 connection
        cur = conn.cursor()

        cur.execute("TRUNCATE TABLE gfw_vessel_registry;")

        buf = io.StringIO()
        writer = csv.writer(buf, delimiter="\t", lineterminator="\n")

        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                writer.writerow([
                    row["mmsi"].strip(),
                    int(row["year"]) if row["year"].strip() else 0,
                    _parse_str(row.get("flag_ais", "")),
                    _parse_str(row.get("flag_registry", "")),
                    _parse_str(row.get("flag_gfw", "")),
                    _parse_str(row.get("vessel_class_inferred", "")),
                    _parse_float(row.get("vessel_class_inferred_score", "")),
                    _parse_str(row.get("vessel_class_registry", "")),
                    _parse_str(row.get("vessel_class_gfw", "")),
                    _parse_bool(row.get("self_reported_fishing_vessel", "")),
                    _parse_float(row.get("length_m_gfw", "")),
                    _parse_float(row.get("engine_power_kw_gfw", "")),
                    _parse_float(row.get("tonnage_gt_gfw", "")),
                    _parse_str(row.get("registries_listed", "")),
                    _parse_float(row.get("active_hours", "")),
                    _parse_float(row.get("fishing_hours", "")),
                ])

        buf.seek(0)
        cur.copy_from(
            buf,
            "gfw_vessel_registry",
            sep="\t",
            null="None",
            columns=col_order,
        )
        conn.commit()

        cur.execute(
            "REFRESH MATERIALIZED VIEW CONCURRENTLY gfw_vessel_latest;"
        )
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM gfw_vessel_registry;")
        count = cur.fetchone()[0]

    elapsed = time.time() - t0
    log.info("gfw_registry.load.done", rows=count, elapsed_s=round(elapsed, 1))
    return {"rows_loaded": count, "elapsed_s": round(elapsed, 1)}


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


def _enrich_with_gfw(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join rows against gfw_vessel_latest for flag/geartype/dimension enrichment.

    One bulk SELECT — O(1) DB round-trip regardless of batch size.
    Fields from GFW take precedence only when the row's existing value is
    absent/unknown, so live AIS name/speed data is never overwritten.
    """
    if not rows:
        return rows
    mmsis = [r["mmsi"] for r in rows if r.get("mmsi")]
    if not mmsis:
        return rows

    with SyncSession() as session:
        result = session.execute(
            text("""
                SELECT mmsi, flag_gfw, vessel_class_gfw,
                       length_m_gfw, engine_power_kw_gfw, tonnage_gt_gfw,
                       fishing_hours, active_hours, registries_listed,
                       self_reported_fishing_vessel
                FROM gfw_vessel_latest
                WHERE mmsi = ANY(:mmsis)
            """),
            {"mmsis": mmsis},
        ).fetchall()

    gfw_map = {r.mmsi: r for r in result}

    enriched = []
    for row in rows:
        rec = dict(row)
        gfw = gfw_map.get(rec.get("mmsi", ""))
        if gfw:
            if not rec.get("flag") or rec["flag"] in ("UNK", ""):
                rec["flag"] = gfw.flag_gfw or rec.get("flag", "UNK")
            if not rec.get("vessel_type") or rec["vessel_type"] in ("unknown", "fishing", ""):
                rec["vessel_type"] = gfw.vessel_class_gfw or rec.get("vessel_type", "unknown")
            rec["gfw_geartype"] = gfw.vessel_class_gfw
            rec["gfw_flag"] = gfw.flag_gfw
            rec["gfw_length_m"] = gfw.length_m_gfw
            rec["gfw_engine_kw"] = gfw.engine_power_kw_gfw
            rec["gfw_tonnage_gt"] = gfw.tonnage_gt_gfw
            rec["gfw_fishing_hours"] = gfw.fishing_hours
            rec["gfw_active_hours"] = gfw.active_hours
            rec["gfw_registries"] = gfw.registries_listed
            rec["gfw_self_reported_fishing"] = gfw.self_reported_fishing_vessel
        enriched.append(rec)
    return enriched


def _upsert_vessels_sync(rows: list[dict[str, Any]]) -> None:
    """Bulk-upsert vessel rows using sync SQLAlchemy + psycopg2."""
    with SyncSession() as session:
        for row in rows:
            row = dict(row)  # copy so we don't mutate the caller's dict
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
    """Truncate + re-insert IUU blacklist records inside a Redis lock.

    The lock prevents two concurrent beat invocations from interleaving their
    DELETE + INSERT sequences, which would produce duplicate rows. The DB
    transaction ensures DELETE + INSERT are committed atomically — if commit
    fails, both operations are rolled back and the existing data is preserved.
    """
    with _sync_redis.lock("lock:sync_iuu_blacklist", timeout=120, blocking_timeout=10):
        with SyncSession() as session:
            try:
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
            except Exception:
                session.rollback()
                raise


def _replace_sanctioned_vessels_sync(
    entries: list[dict[str, Any]],
) -> None:
    """Truncate + re-insert sanctioned vessel records inside a Redis lock."""
    with _sync_redis.lock("lock:sync_sanctioned_vessels", timeout=120, blocking_timeout=10):
        with SyncSession() as session:
            try:
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
            except Exception:
                session.rollback()
                raise


def _insert_tracks_sync(rows: list[dict[str, Any]]) -> None:
    """Bulk-insert ping rows into vessel_tracks in ONE round-trip.

    Uses a single ``INSERT ... VALUES (…), (…), …`` instead of N individual
    statements. vessel_tracks is append-only so there is no ON CONFLICT clause.
    """
    if not rows:
        return
    values = [
        {
            "mmsi":      row["mmsi"],
            "position":  func.ST_SetSRID(func.ST_Point(row["lon"], row["lat"]), 4326),
            "sog":       row["sog"],
            "cog":       row.get("cog", 0.0),
            "timestamp": row["timestamp"],
            "source":    row.get("source", "unknown"),
        }
        for row in rows
    ]
    with SyncSession() as session:
        session.execute(pg_insert(VesselTrack).values(values))
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
            # SET NX atomically records first_ts only if the key is new.
            # Without NX, two workers processing the same pair concurrently
            # would both write their own first_ts, making the later write
            # win and shortening the apparent rendezvous duration.
            initial = ujson.dumps({
                "first_ts": now.isoformat(),
                "type_a": a.vessel_type,
                "type_b": b.vessel_type,
            })
            _sync_redis.set(key, initial, ex=REDIS_TTL, nx=True)
            raw = _sync_redis.getex(key, ex=REDIS_TTL)  # resets TTL on read
            state = ujson.loads(raw) if raw else ujson.loads(initial)
            first_ts = datetime.fromisoformat(state["first_ts"])
            duration_h = (now - first_ts).total_seconds() / 3600.0

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
    import math  # noqa: PLC0415,F401
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

    Replaces the former N+1 pattern (2 queries per vessel) with a single
    LATERAL UNNEST query that resolves all vessels in one round-trip:

      - UNNEST passes all (mmsi, lat, lon) tuples as parallel arrays.
      - CROSS JOIN LATERAL fetches the nearest raster cell per vessel using
        the GiST index (ST_DWithin filter + <-> ordering).
      - A correlated subquery collects neighbour SST values for front detection
        in the same pass.

    Returns dict mmsi → EnvironmentalContext dataclass.
    """
    from spyhop.analytics.context_fusion import (  # noqa: PLC0415
        EnvironmentalContext, detect_sst_front,
    )

    if not vessels:
        return {}

    radius_m = search_radius_deg * 111_000
    neighbour_m = 3 * 111_000  # ~3° radius for SST gradient neighbours

    mmsis = [v.mmsi for v in vessels]
    lats = [float(v.lat) for v in vessels]
    lons = [float(v.lon) for v in vessels]

    with SyncSession() as session:
        rows = session.execute(
            text(
                """
                SELECT
                    v.mmsi,
                    r.sst_celsius,
                    r.wave_height_m,
                    r.wind_speed_kn,
                    (
                        SELECT array_agg(n.sst_celsius)
                        FROM environment_raster n
                        WHERE ST_DWithin(
                            n.position::geography,
                            ST_SetSRID(ST_MakePoint(v.lon::float8, v.lat::float8), 4326)::geography,
                            :neighbour_m
                        )
                        AND n.sst_celsius > -999
                        LIMIT 16
                    ) AS neighbour_sst
                FROM
                    unnest(:mmsis, :lats, :lons) AS v(mmsi, lat, lon)
                CROSS JOIN LATERAL (
                    SELECT sst_celsius, wave_height_m, wind_speed_kn
                    FROM environment_raster
                    WHERE ST_DWithin(
                        position::geography,
                        ST_SetSRID(ST_MakePoint(v.lon::float8, v.lat::float8), 4326)::geography,
                        :radius_m
                    )
                    ORDER BY
                        position <-> ST_SetSRID(ST_MakePoint(v.lon::float8, v.lat::float8), 4326)
                    LIMIT 1
                ) r
                """
            ),
            {
                "mmsis": mmsis,
                "lats": lats,
                "lons": lons,
                "radius_m": radius_m,
                "neighbour_m": neighbour_m,
            },
        ).fetchall()

    result: dict[str, Any] = {}
    for row in rows:
        sst = float(row.sst_celsius)
        wave = float(row.wave_height_m)
        wind = float(row.wind_speed_kn)
        nearby_sst = [float(x) for x in (row.neighbour_sst or [])]
        at_front = detect_sst_front(sst, nearby_sst)
        result[row.mmsi] = EnvironmentalContext(
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

    result: dict[str, Any] = {}
    miss_mmsi: list[str] = []

    for v in vessels:
        key = f"vessel_profile:{v.mmsi}"
        cached = _sync_redis.get(key)
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
            _sync_redis.setex(
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
@celery_app.task(
    name="spyhop.worker.tasks.prune_vessel_snapshots",
    soft_time_limit=60,
    time_limit=90,
)
def prune_vessel_snapshots(retain_days: int = 90) -> dict[str, Any]:
    """Delete vessel_position_snapshots older than retain_days (default 90).

    At ~1 000 vessels × 24 snapshots/day, 90 days ≈ 2.2 M rows — small enough
    that a simple DELETE with a timestamped index scan completes in seconds.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    with SyncSession() as session:
        result = session.execute(
            text("DELETE FROM vessel_position_snapshots WHERE snapped_at < :cutoff"),
            {"cutoff": cutoff},
        )
        session.commit()
        deleted = result.rowcount
    log.info("prune_vessel_snapshots deleted=%d retain_days=%d", deleted, retain_days)
    return {"status": "ok", "deleted": deleted}


# ---------------------------------------------------------------------------
# Task: refresh_ecological_masks  (nightly)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="spyhop.worker.tasks.refresh_ecological_masks",
    soft_time_limit=300,
    time_limit=360,
)
def refresh_ecological_masks() -> dict[str, Any]:
    """Pre-materialise ecological risk masks for every active H3 res-7 cell.

    Runs nightly at 00:15 UTC. For each active cetacean corridor and spawning
    ground, computes the set of H3 res-7 cells that overlap the bounding box
    and writes a compact JSON blob to Redis (key ``eco:h3:{cell}``, TTL 26 h).

    The spatial worker reads these keys in its hot path (one HGETALL per
    unique cell per batch) so the brain can evaluate ecological rules without
    any PostGIS or external-API calls on the critical path.

    Redis schema per cell::

        eco:h3:{cell} → HASH {
            "corridors":    '[{"id":…,"label":…,"species":[…],"peak":0.72}]',
            "spawning":     '[{"id":…,"label":…,"species":[…],"peak":true}]',
            "max_endanger": "0.9",   # max endangerment_weight of active corridors
        }
    """
    from h3 import LatLngPoly, h3shape_to_cells  # noqa: PLC0415

    from spyhop.analytics.ecological import (  # noqa: PLC0415
        active_corridors,
        active_spawning_grounds,
    )

    today = datetime.now(timezone.utc).date()
    corridors = active_corridors(today)
    grounds   = active_spawning_grounds(today)

    log.info(
        "refresh_ecological_masks date=%s active_corridors=%d active_spawning=%d",
        today, len(corridors), len(grounds),
    )

    # Build cell → payload map
    cell_corridors: dict[str, list[dict]] = defaultdict(list)
    cell_spawning:  dict[str, list[dict]] = defaultdict(list)

    for corridor in corridors:
        peak = corridor.season.peak_fraction(today)
        poly = LatLngPoly([
            (corridor.north, corridor.west),
            (corridor.north, corridor.east),
            (corridor.south, corridor.east),
            (corridor.south, corridor.west),
        ])
        for cell in h3shape_to_cells(poly, 7):
            cell_corridors[cell].append({
                "id":      corridor.id,
                "label":   corridor.label,
                "species": list(corridor.species),
                "peak":    round(peak, 4),
                "endanger": corridor.endangerment_weight,
            })

    for ground in grounds:
        poly = LatLngPoly([
            (ground.north, ground.west),
            (ground.north, ground.east),
            (ground.south, ground.east),
            (ground.south, ground.west),
        ])
        for cell in h3shape_to_cells(poly, 7):
            cell_spawning[cell].append({
                "id":      ground.id,
                "label":   ground.label,
                "species": list(ground.species),
            })

    all_cells = set(cell_corridors) | set(cell_spawning)
    ttl = 26 * 3600   # 26 h — survives until next nightly refresh

    pipe = _sync_redis.pipeline(transaction=False)
    for cell in all_cells:
        cors = cell_corridors.get(cell, [])
        spwn = cell_spawning.get(cell, [])
        max_endanger = max((c["endanger"] for c in cors), default=0.0)
        key = f"eco:h3:{cell}"
        pipe.hset(key, mapping={
            "corridors":    ujson.dumps(cors),
            "spawning":     ujson.dumps(spwn),
            "max_endanger": str(max_endanger),
        })
        pipe.expire(key, ttl)
    pipe.execute()

    log.info("refresh_ecological_masks cells_written=%d", len(all_cells))
    return {
        "status":         "ok",
        "date":           str(today),
        "cells_written":  len(all_cells),
        "corridors_active": len(corridors),
        "spawning_active":  len(grounds),
    }
