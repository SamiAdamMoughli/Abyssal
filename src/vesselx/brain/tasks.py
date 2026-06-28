"""Celery tasks for the VesselX brain — rule evaluation and alert emission.

Two tasks:

  evaluate_spatialized_batch  — called by Celery beat every 30 s; drains the
                                spatialized stream in a loop until empty or the
                                25 s soft time limit is hit.  Stale PEL messages
                                (from crashed workers) are reclaimed first via
                                XAUTOCLAIM so no telemetry is silently dropped.

  evaluate_vessel_by_mmsi     — on-demand evaluation for a single vessel,
                                called from the brain management API or
                                analyst tools.
"""
from __future__ import annotations

import redis as sync_redis_lib
import ujson
from celery.exceptions import SoftTimeLimitExceeded
from celery.utils.log import get_task_logger
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from spyhop.config import get_settings
from spyhop.worker.celery_app import celery_app
from vesselx.brain.evaluator import AlertFinding, evaluate
from vesselx.ml import behavior, kinematic, rendezvous, scorer, spoofing

log = get_task_logger(__name__)
settings = get_settings()

STREAM_IN = "vesselx:telemetry:spatialized"
STREAM_ALERTS = "vesselx:alerts"
ALERT_CHANNEL = "vessel:alerts"  # legacy pub/sub; spyhop WS route listens here
GROUP = "brain-workers"
CONSUMER = "brain-0"
BATCH_SIZE = 50
STREAM_MAXLEN = 10_000
TRACK_MAXLEN = 20   # must match spatial_worker.TRACK_MAXLEN
# Messages unclaimed for longer than this are stolen from crashed workers.
PEL_IDLE_MS = 60_000  # 60 seconds

# Suppress re-fires of the same rule+vessel condition for this window.
# After TTL expires the condition is treated as new and re-opens.
REFIRE_TTL_SECONDS = 4 * 3600

_redis = sync_redis_lib.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Shadow scoring singletons (lazy-init; no cost when ML tables absent)
# ---------------------------------------------------------------------------

_shadow_engine = create_engine(
    settings.SYNC_DATABASE_URL,
    pool_size=2,
    max_overflow=3,
    pool_pre_ping=True,
    pool_recycle=1800,
)
_ShadowSession = sessionmaker(
    bind=_shadow_engine, autocommit=False, autoflush=False
)


def _get_risk_loader():
    from spyhop.ml.serving.loader import get_loader
    return get_loader("risk_scorer")


def _ensure_group() -> None:
    try:
        _redis.xgroup_create(STREAM_IN, GROUP, id="0", mkstream=True)
    except sync_redis_lib.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


# ---------------------------------------------------------------------------
# Alert broadcast
# ---------------------------------------------------------------------------

def _broadcast_batch(findings: list[AlertFinding]) -> None:
    """Write all findings to the alert stream and pub/sub in one pipeline."""
    if not findings:
        return
    pipe = _redis.pipeline(transaction=False)
    for f in findings:
        blob = ujson.dumps(f.as_dict())
        pipe.xadd(
            STREAM_ALERTS,
            {"data": blob},
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )
        pipe.publish(ALERT_CHANNEL, blob)
    pipe.execute()


# ---------------------------------------------------------------------------
# Alert deduplication — suppress re-fires of the same rule+vessel condition
# ---------------------------------------------------------------------------

def _suppression_key(mmsi: str, rule_id: str) -> str:
    return f"vesselx:active:{mmsi}:{rule_id}"


def _filter_and_gate(findings: list[AlertFinding]) -> list[AlertFinding]:
    """Return only findings whose rule+vessel condition is not already active.

    Uses a Redis SETNX pipeline so the entire batch costs one round-trip.
    Each new finding sets a key with REFIRE_TTL_SECONDS TTL; subsequent
    firings of the same rule+vessel hit the existing key and are dropped.
    When the TTL expires the condition is treated as new (re-opens).
    """
    if not findings:
        return []

    pipe = _redis.pipeline(transaction=False)
    for f in findings:
        pipe.setnx(_suppression_key(f.mmsi or "", f.rule_id), f.alert_id)
    is_new: list[bool] = pipe.execute()

    new_findings: list[AlertFinding] = []
    ttl_pipe = _redis.pipeline(transaction=False)
    for f, new in zip(findings, is_new):
        if new:
            new_findings.append(f)
            ttl_pipe.expire(_suppression_key(f.mmsi or "", f.rule_id), REFIRE_TTL_SECONDS)
    ttl_pipe.execute()

    suppressed = len(findings) - len(new_findings)
    if suppressed:
        log.debug("brain.suppressed count=%d", suppressed)

    return new_findings


def _resolve_cleared(mmsi: str, triggered_rule_ids: set[str]) -> None:
    """Delete suppression keys for rules that are no longer triggering.

    This allows a condition that clears (vessel exits MPA, gap closes) to
    re-open immediately on the next evaluation cycle instead of waiting for
    the REFIRE_TTL to expire.
    """
    # Scan only keys scoped to this vessel — avoids a full keyspace scan.
    pattern = f"vesselx:active:{mmsi}:*"
    active_keys: list[str] = _redis.keys(pattern)
    if not active_keys:
        return
    to_delete = [
        k for k in active_keys
        if k.split(":")[-1] not in triggered_rule_ids
    ]
    if to_delete:
        _redis.delete(*to_delete)
        resolved = [k.split(":")[-1] for k in to_delete]
        log.info("brain.resolved mmsi=%s rules=%s", mmsi, resolved)


# ---------------------------------------------------------------------------
# PEL drain — reclaim messages from crashed workers
# ---------------------------------------------------------------------------

def _reclaim_stale_pel() -> list[tuple[str, dict]]:
    """Steal PEL messages idle > PEL_IDLE_MS from any consumer in the group.

    Uses XAUTOCLAIM (Redis ≥ 6.2) to re-assign stale messages to this
    consumer so they are processed and ACKed on this cycle instead of
    sitting in a dead consumer's PEL indefinitely.

    Returns a flat list of (msg_id, fields) pairs ready to pass to
    _process_messages().
    """
    try:
        result = _redis.xautoclaim(
            STREAM_IN,
            GROUP,
            CONSUMER,
            min_idle_time=PEL_IDLE_MS,
            start_id="0-0",
            count=BATCH_SIZE,
        )
        # redis-py returns [next_start_id, [(id, fields), ...], [deleted_ids]]
        reclaimed: list[tuple[str, dict]] = result[1] if result and len(result) >= 2 else []
        if reclaimed:
            log.info("brain.pel_reclaim count=%d", len(reclaimed))
        return reclaimed
    except Exception as exc:
        log.warning("brain.pel_reclaim_error err=%s", exc)
        return []


# ---------------------------------------------------------------------------
# ML enrichment — injects behavioral signals into vessel_state dicts
# ---------------------------------------------------------------------------

def _ml_enrich_batch(parsed: list[tuple[str, dict]]) -> None:
    """Mutate each vessel_state in-place with ML-derived signal fields.

    One Redis pipeline round-trip reads all track ring buffers; every
    subsequent computation is pure Python with no further I/O.

    Fields injected (matching brain/rules.py predicate keys):
      behavior_status, behavior_confidence,
      spoofing_flag, spoofing_max_speed_kn,
      rendezvous_meeting_class, rendezvous_partner_type, rendezvous_duration_hours,
      risk_score, top_reason_label
    """
    mmsis = [vs.get("mmsi", "") for _, vs in parsed]

    # Pipeline all LRANGE calls — one network round-trip for the whole batch
    pipe = _redis.pipeline(transaction=False)
    for mmsi in mmsis:
        pipe.lrange(f"vessel:{mmsi}:track", 0, TRACK_MAXLEN - 1)
    raw_tracks: list[list[str]] = pipe.execute()

    track_map: dict[str, list[dict]] = {}
    for mmsi, blobs in zip(mmsis, raw_tracks):
        pts: list[dict] = []
        for b in (blobs or []):
            try:
                pts.append(ujson.loads(b))
            except Exception:
                pass
        track_map[mmsi] = pts

    for _, vessel_state in parsed:
        mmsi = vessel_state.get("mmsi", "")
        feats = kinematic.extract(track_map.get(mmsi, []))

        bhv = behavior.classify(feats)
        vessel_state["behavior_status"] = bhv.status
        vessel_state["behavior_confidence"] = bhv.confidence

        spoof = spoofing.assess(feats)
        vessel_state["spoofing_flag"] = spoof.flag
        vessel_state["spoofing_max_speed_kn"] = spoof.max_implied_speed_kn

        rdv = rendezvous.assess(
            mmsi=mmsi,
            h3_index=vessel_state.get("h3_index"),
            vessel_type=vessel_state.get("vessel_type"),
            sog=float(vessel_state.get("sog") or 0.0),
            redis_client=_redis,
        )
        vessel_state["rendezvous_meeting_class"] = rdv.meeting_class
        vessel_state["rendezvous_partner_type"] = rdv.partner_type
        vessel_state["rendezvous_duration_hours"] = rdv.duration_hours

        risk = scorer.compute(vessel_state)
        vessel_state["risk_score"] = risk.score
        vessel_state["top_reason_label"] = risk.top_reason_label


# ---------------------------------------------------------------------------
# Core message processor (shared by beat task and PEL drain path)
# ---------------------------------------------------------------------------

def _process_messages(
    messages: list[tuple[str, dict]],
) -> tuple[list[AlertFinding], list[str], list[dict]]:
    """Parse, IUU-enrich, and rule-evaluate a batch of stream messages.

    Returns (findings, ack_ids, vessel_states).  vessel_states is the list
    of enriched dicts, passed to shadow scoring after broadcast.
    Malformed and failed-eval messages are ACKed so they never block stream.
    """
    if not messages:
        return [], [], []

    parsed: list[tuple[str, dict]] = []
    bad_ids: list[str] = []

    for msg_id, fields in messages:
        try:
            state: dict = ujson.loads(fields["data"])
            parsed.append((msg_id, state))
        except Exception as exc:
            log.error("brain.parse_error msg_id=%s err=%s", msg_id, exc)
            bad_ids.append(msg_id)

    # ACK malformed messages immediately — nothing useful to retry
    if bad_ids:
        _redis.xack(STREAM_IN, GROUP, *bad_ids)

    if not parsed:
        return [], [], []

    # Batch IUU lookup — one SMISMEMBER instead of N SISMEMBER calls
    mmsis = [vs.get("mmsi", "") for _, vs in parsed]
    iuu_flags = _redis.smismember("iuu:mmsi_set", mmsis)
    iuu_lookup = {m: bool(flag) for m, flag in zip(mmsis, iuu_flags)}

    for _, vessel_state in parsed:
        vessel_state["on_iuu_blacklist"] = iuu_lookup.get(
            vessel_state.get("mmsi", ""), False
        )

    # ML enrichment: behavior, spoofing, rendezvous, composite risk score
    try:
        _ml_enrich_batch(parsed)
    except Exception as exc:
        log.error("brain.ml_enrich_error err=%s", exc)

    raw_findings: list[AlertFinding] = []
    ack_ids: list[str] = []
    vessel_states: list[dict] = []

    for msg_id, vessel_state in parsed:
        mmsi = vessel_state.get("mmsi", "")
        try:
            vessel_findings = evaluate(vessel_state)
            triggered_ids = {f.rule_id for f in vessel_findings}
            _resolve_cleared(mmsi, triggered_ids)
            raw_findings.extend(vessel_findings)
        except Exception as exc:
            log.error("brain.eval_error msg_id=%s err=%s", msg_id, exc)
        # ACK regardless of eval outcome — a persistent rule exception would
        # otherwise pin the message in the PEL and block stream progress.
        ack_ids.append(msg_id)
        vessel_states.append(vessel_state)

    findings = _filter_and_gate(raw_findings)
    return findings, ack_ids, vessel_states


# ---------------------------------------------------------------------------
# Shadow scoring — runs ML model alongside rule evaluation, no-op if absent
# ---------------------------------------------------------------------------

def _shadow_score(vessel_states: list[dict]) -> None:
    """Fire-and-forget shadow scoring.  Never raises, never blocks alerts."""
    if not vessel_states:
        return
    try:
        from spyhop.ml.shadow import score_batch
        loader = _get_risk_loader()
        score_batch(vessel_states, loader, _redis, _ShadowSession)
    except Exception as exc:
        log.debug("brain.shadow_score_error err=%s", exc)


# ---------------------------------------------------------------------------
# Beat task: consume spatialized stream → evaluate → broadcast
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="brain.evaluate_spatialized_batch",
    max_retries=3,
    default_retry_delay=5,
    # Both limits are set just under the 30 s beat interval.
    # soft_time_limit raises SoftTimeLimitExceeded (caught below) so we can
    # return a partial result cleanly; time_limit sends SIGKILL as a backstop.
    soft_time_limit=25,
    time_limit=28,
)
def evaluate_spatialized_batch(self) -> dict:
    """Drain the spatialized telemetry stream and run the full rule cycle.

    Execution flow each invocation:
      1. Reclaim any stale PEL messages left by crashed workers (XAUTOCLAIM).
      2. Loop: read a batch of new messages → evaluate → ACK → repeat.
         Exits when the stream is empty or the 25 s soft limit fires.
    """
    _ensure_group()

    total_evaluated = 0
    total_alerts = 0

    try:
        # --- Step 1: reclaim stale PEL messages from crashed workers ----------
        reclaimed = _reclaim_stale_pel()
        if reclaimed:
            findings, ack_ids, vstates = _process_messages(reclaimed)
            _broadcast_batch(findings)
            _shadow_score(vstates)
            if ack_ids:
                _redis.xack(STREAM_IN, GROUP, *ack_ids)
            total_evaluated += len(ack_ids)
            total_alerts += len(findings)

        # --- Step 2: drain new messages until stream empty or time budget hit -
        while True:
            results = _redis.xreadgroup(
                groupname=GROUP,
                consumername=CONSUMER,
                streams={STREAM_IN: ">"},
                count=BATCH_SIZE,
                # Non-blocking — beat schedule provides the cadence
            )
            if not results:
                break

            _, messages = results[0]  # type: ignore[index]
            findings, ack_ids, vstates = _process_messages(messages)
            _broadcast_batch(findings)
            _shadow_score(vstates)
            if ack_ids:
                _redis.xack(STREAM_IN, GROUP, *ack_ids)
            total_evaluated += len(ack_ids)
            total_alerts += len(findings)

            if len(messages) < BATCH_SIZE:
                break  # stream is fully drained for this cycle

    except SoftTimeLimitExceeded:
        # Beat interval is 30 s; we consumed as much as we could in 25 s.
        # The remainder will be picked up on the next cycle.
        log.warning(
            "brain.soft_time_limit_hit evaluated=%d alerts=%d",
            total_evaluated, total_alerts,
        )

    if total_alerts:
        log.info(
            "brain.cycle evaluated=%d alerts=%d",
            total_evaluated, total_alerts,
        )

    return {"evaluated": total_evaluated, "alerts": total_alerts}


# ---------------------------------------------------------------------------
# On-demand task: evaluate a single vessel by MMSI
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="brain.evaluate_vessel_by_mmsi",
    max_retries=2,
    default_retry_delay=2,
)
def evaluate_vessel_by_mmsi(self, mmsi: str) -> dict:
    """Evaluate a single vessel against the full rulebook.

    Reads the vessel's current state from the Redis hot cache written by the
    spatial worker. Returns immediately with reason='no_cached_state' if the
    vessel is not yet in Redis (not yet seen or TTL expired).
    """
    cached = _redis.hgetall(f"vessel:{mmsi}")

    if not cached:
        return {"mmsi": mmsi, "alerts": 0, "reason": "no_cached_state"}

    cell = cached.get("h3_index")
    sog  = float(cached.get("sog", 0))

    # Fetch IUU flag, track ring buffer, and ecological mask in one pipeline
    pipe = _redis.pipeline(transaction=False)
    pipe.sismember("iuu:mmsi_set", mmsi)
    pipe.lrange(f"vessel:{mmsi}:track", 0, TRACK_MAXLEN - 1)
    if cell:
        pipe.hgetall(f"eco:h3:{cell}")
    p_results = pipe.execute()

    on_iuu: bool = bool(p_results[0])
    track_blobs: list[str] = p_results[1] or []
    eco: dict = (p_results[2] if cell else {}) or {}

    corridors = ujson.loads(eco.get("corridors", "[]")) if eco else []
    spawning  = ujson.loads(eco.get("spawning",  "[]")) if eco else []
    endanger  = float(eco.get("max_endanger", "0")) if eco else 0.0
    season_pk = max(
        (c.get("peak", 0.0) for c in corridors), default=0.0
    )

    vessel_state = {
        "mmsi": mmsi,
        "lat": float(cached.get("lat", 0)),
        "lon": float(cached.get("lon", 0)),
        "sog": sog,
        "cog": float(cached.get("cog", 0)),
        "h3_index": cell,
        "vessel_type": cached.get("vessel_type", ""),
        "in_protected_area": (
            cached.get("in_protected_area", "").lower() == "true"
        ),
        "border_skirting": (
            cached.get("border_skirting", "").lower() == "true"
        ),
        "on_iuu_blacklist": on_iuu,
        "ais_gap_hours": float(cached.get("ais_gap_hours", 0)),
        "nearest_mpa_nm": float(cached.get("nearest_mpa_nm", -1)),
        "time_in_zone_hours": float(cached.get("time_in_zone_hours", 0)),
        "is_dark_candidate": (
            cached.get("is_dark_candidate", "").lower() == "true"
        ),
        # Ecological signals
        "in_cetacean_corridor": bool(corridors),
        "corridor_species": [
            s for c in corridors for s in c.get("species", [])
        ],
        "corridor_season_peak": season_pk,
        "endangerment_weight": endanger,
        "in_spawning_ground": bool(spawning),
        "spawning_species": [
            s for g in spawning for s in g.get("species", [])
        ],
        "whale_strike_risk": round(
            min(sog / 20.0, 1.0) * season_pk * endanger, 4
        ),
    }

    # Run ML enrichment against the track ring buffer for this single vessel
    pts: list[dict] = []
    for b in track_blobs:
        try:
            pts.append(ujson.loads(b))
        except Exception:
            pass

    feats = kinematic.extract(pts)

    bhv = behavior.classify(feats)
    vessel_state["behavior_status"] = bhv.status
    vessel_state["behavior_confidence"] = bhv.confidence

    spoof = spoofing.assess(feats)
    vessel_state["spoofing_flag"] = spoof.flag
    vessel_state["spoofing_max_speed_kn"] = spoof.max_implied_speed_kn

    rdv = rendezvous.assess(
        mmsi=mmsi,
        h3_index=vessel_state.get("h3_index"),
        vessel_type=vessel_state.get("vessel_type"),
        sog=vessel_state["sog"],
        redis_client=_redis,
    )
    vessel_state["rendezvous_meeting_class"] = rdv.meeting_class
    vessel_state["rendezvous_partner_type"] = rdv.partner_type
    vessel_state["rendezvous_duration_hours"] = rdv.duration_hours

    risk = scorer.compute(vessel_state)
    vessel_state["risk_score"] = risk.score
    vessel_state["top_reason_label"] = risk.top_reason_label

    raw_findings = evaluate(vessel_state)
    triggered_ids = {f.rule_id for f in raw_findings}
    _resolve_cleared(mmsi, triggered_ids)
    findings = _filter_and_gate(raw_findings)
    _broadcast_batch(findings)

    return {"mmsi": mmsi, "alerts": len(findings)}
