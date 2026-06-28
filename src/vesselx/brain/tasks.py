"""Celery tasks for the VesselX brain — rule evaluation and alert emission.

Three tasks:

  evaluate_spatialized_batch  — called by Celery beat every 30 s; reads up to
                                BATCH_SIZE records from the spatialized stream
                                and runs the full rule evaluation cycle.

  evaluate_vessel_by_mmsi     — on-demand evaluation for a single vessel, called
                                from the brain management API or analyst tools.

  broadcast_alert             — internal helper (not registered as a beat task);
                                persists an AlertFinding to the Redis alert stream
                                and the legacy vessel:alerts pub/sub channel so
                                the existing WebSocket route picks it up.
"""
from __future__ import annotations

import logging

import redis as sync_redis_lib
import ujson
from celery.utils.log import get_task_logger

from spyhop.config import get_settings
from spyhop.worker.celery_app import celery_app
from vesselx.brain.evaluator import AlertFinding, evaluate

log      = get_task_logger(__name__)
settings = get_settings()

STREAM_IN      = "vesselx:telemetry:spatialized"
STREAM_ALERTS  = "vesselx:alerts"
ALERT_CHANNEL  = "vessel:alerts"   # legacy pub/sub — spyhop WS route listens here
GROUP          = "brain-workers"
CONSUMER       = "brain-0"
BATCH_SIZE     = 50
STREAM_MAXLEN  = 10_000

_redis = sync_redis_lib.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
)


def _ensure_group() -> None:
    try:
        _redis.xgroup_create(STREAM_IN, GROUP, id="0", mkstream=True)
    except sync_redis_lib.ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


# ---------------------------------------------------------------------------
# Alert broadcast
# ---------------------------------------------------------------------------

def _broadcast(finding: AlertFinding) -> None:
    """Write alert to the alert Stream and pub/sub channel (non-blocking)."""
    blob = ujson.dumps(finding.as_dict())
    pipe = _redis.pipeline(transaction=False)
    pipe.xadd(
        STREAM_ALERTS,
        {"data": blob},
        maxlen=STREAM_MAXLEN,
        approximate=True,
    )
    pipe.publish(ALERT_CHANNEL, blob)
    pipe.execute()


# ---------------------------------------------------------------------------
# Beat task: consume spatialized stream → evaluate → broadcast
# ---------------------------------------------------------------------------

@celery_app.task(
    bind=True,
    name="brain.evaluate_spatialized_batch",
    max_retries=3,
    default_retry_delay=5,
)
def evaluate_spatialized_batch(self) -> dict:
    """Process a batch from the spatialized telemetry stream."""
    _ensure_group()

    results = _redis.xreadgroup(
        groupname=GROUP,
        consumername=CONSUMER,
        streams={STREAM_IN: ">"},
        count=BATCH_SIZE,
        # No block arg = non-blocking; called on a 30s Celery beat schedule
    )

    if not results:
        return {"evaluated": 0, "alerts": 0}

    total_alerts = 0
    evaluated    = 0

    for _stream_name, messages in results:
        for msg_id, fields in messages:
            try:
                vessel_state: dict = ujson.loads(fields["data"])

                # Augment with blacklist flag (O(1) Redis set membership)
                mmsi = vessel_state.get("mmsi", "")
                vessel_state["on_iuu_blacklist"] = bool(
                    _redis.sismember("iuu:mmsi_set", mmsi)
                )

                findings = evaluate(vessel_state)
                for f in findings:
                    _broadcast(f)

                total_alerts += len(findings)
                evaluated    += 1

                _redis.xack(STREAM_IN, GROUP, msg_id)

            except Exception as exc:
                log.error(
                    "brain.eval_error msg_id=%s err=%s", msg_id, exc
                )

    if total_alerts:
        log.info("brain.cycle evaluated=%d alerts=%d", evaluated, total_alerts)

    return {"evaluated": evaluated, "alerts": total_alerts}


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
        # Try the H3 layer from ais_stream.py as a fallback
        cell = _redis.hget(f"h3:*", mmsi)  # noqa: best-effort
        if not cell:
            return {"mmsi": mmsi, "alerts": 0, "reason": "no_cached_state"}

    vessel_state = {
        "mmsi":            mmsi,
        "lat":             float(cached.get("lat", 0)),
        "lon":             float(cached.get("lon", 0)),
        "sog":             float(cached.get("sog", 0)),
        "h3_index":        cached.get("h3_index"),
        "risk_score":      float(cached.get("risk_score", 0)),
        "behavior_status": cached.get("behavior_status", ""),
        "behavior_confidence": float(cached.get("behavior_confidence", 0)),
        "in_protected_area": cached.get("in_protected_area", "").lower() == "true",
        "border_skirting": cached.get("border_skirting", "").lower() == "true",
        "on_iuu_blacklist": bool(_redis.sismember("iuu:mmsi_set", mmsi)),
        "ais_gap_hours":   float(cached.get("ais_gap_hours", 0)),
        "spoofing_flag":   cached.get("spoofing_flag", "").lower() == "true",
    }

    findings = evaluate(vessel_state)
    for f in findings:
        _broadcast(f)

    return {"mmsi": mmsi, "alerts": len(findings)}
