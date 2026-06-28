"""WebSocket endpoint — streams brain rule-evaluation alerts to the operator UI.

Subscribes to the ``vessel:alerts`` Redis pub/sub channel and forwards each
alert JSON blob to the connected browser client.  The brain Celery task
publishes here whenever a rule fires (MPA incursion, AIS gap, spoofing, etc.).

Protocol: server sends JSON text frames; client is read-only.

Alert shape (matches vesselx.brain.evaluator.AlertFinding.as_dict):
  {
    "alert_id":     "<uuid>",
    "rule_id":      "mpa_incursion",
    "rule_label":   "Marine Protected Area Incursion",
    "severity":     "critical",          // info | warning | alert | critical
    "message":      "Vessel … detected inside …",
    "mmsi":         "123456789",
    "lat":          -1.23,
    "lon":          -91.45,
    "h3_index":     "872a1008affffff",   // H3 res-7 cell of the vessel
    "triggered_at": "2026-06-26T12:00:00+00:00"
  }
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from spyhop.api.deps import get_redis
from spyhop.cache.redis_client import VESSEL_ALERTS_CHANNEL, RedisClient
from spyhop.logging_config import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["alerts"])


@router.websocket("/ws/alerts")
async def alerts_ws(
    websocket: WebSocket,
    redis: RedisClient = Depends(get_redis),
) -> None:
    """Stream real-time brain alerts to the operator desktop client.

    Each frame is a JSON object describing one triggered rule finding.
    The client uses ``h3_index`` to flash the corresponding map hexagon and
    appends the finding to the alert sidebar.
    """
    await websocket.accept()
    log.info("ws.alerts.connected", client=str(websocket.client))

    try:
        async for message in redis.subscribe(VESSEL_ALERTS_CHANNEL):
            try:
                await websocket.send_text(message)
            except WebSocketDisconnect:
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("ws.alerts.send_error", error=str(exc))
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("ws.alerts.error", error=str(exc))
    finally:
        log.info("ws.alerts.disconnected", client=str(websocket.client))
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
