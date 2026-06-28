"""VesselX Brain Service — management API.

Exposes operator and integration tooling for the rule evaluation layer:
  GET  /health              — liveness probe
  GET  /rules               — full rule catalogue with severity labels
  GET  /capabilities        — detector summary and queue names
  POST /evaluate/{mmsi}     — trigger on-demand evaluation for one vessel
  GET  /alerts/stream       — recent entries from the vesselx:alerts stream
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException

from vesselx import __version__
from vesselx.brain.rules import RULES, Severity
from vesselx.brain.tasks import STREAM_ALERTS

app = FastAPI(
    title="VesselX Rule & Behavioral Anomaly Service",
    version=__version__,
    description=(
        "Management plane for the VesselX brain: rule catalogue inspection, "
        "on-demand vessel evaluation, and recent alert stream access."
    ),
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "vesselx-brain", "version": __version__}


@app.get("/rules")
async def list_rules() -> dict[str, object]:
    """Return every registered rule with id, label, and severity."""
    return {
        "count": len(RULES),
        "rules": [
            {"id": r.id, "label": r.label, "severity": r.severity.value}
            for r in RULES
        ],
    }


@app.get("/capabilities")
async def capabilities() -> dict[str, object]:
    severity_counts = {
        s.value: sum(1 for r in RULES if r.severity == s)
        for s in Severity
    }
    return {
        "service":         "vesselx-brain",
        "version":         __version__,
        "rule_count":      len(RULES),
        "severity_counts": severity_counts,
        "detectors":       [r.id for r in RULES],
        "queues": [
            "brain.evaluate_spatialized_batch",
            "brain.evaluate_vessel_by_mmsi",
        ],
        "streams": {
            "in":  "vesselx:telemetry:spatialized",
            "out": STREAM_ALERTS,
        },
    }


@app.post("/evaluate/{mmsi}")
async def trigger_evaluation(mmsi: str) -> dict[str, object]:
    """Queue an on-demand rule evaluation for a specific vessel."""
    try:
        from vesselx.brain.tasks import evaluate_vessel_by_mmsi
        task = evaluate_vessel_by_mmsi.delay(mmsi)
        return {"task_id": task.id, "mmsi": mmsi, "status": "queued"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/alerts/stream")
async def recent_alerts(count: int = 50) -> dict[str, object]:
    """Return the most recent alert records from the vesselx:alerts stream."""
    import redis.asyncio as aioredis
    import ujson
    from spyhop.cache.redis_client import get_pool

    r = aioredis.Redis(connection_pool=get_pool())
    try:
        messages = await r.xrevrange(STREAM_ALERTS, count=min(count, 200))
        alerts = [ujson.loads(fields["data"]) for _, fields in messages]
    except Exception:
        alerts = []

    return {"count": len(alerts), "alerts": alerts}
