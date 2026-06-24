"""WebSocket endpoint — streams live vessel updates from Redis PubSub.

Lifecycle:
  1. Client connects → handler subscribes to the Redis ``vessel:updates`` channel.
  2. Each Celery ``fetch_and_score_vessels`` run publishes a JSON array of
     scored vessels to that channel.
  3. Handler forwards every message to the connected WebSocket client.
  4. On disconnect (normal or error) the ``try/finally`` in
     ``RedisClient.subscribe`` guarantees the PubSub is unsubscribed and
     closed — no server-side resource leak.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from spyhop.api.deps import get_redis
from spyhop.cache.redis_client import VESSEL_UPDATES_CHANNEL, RedisClient
from spyhop.logging_config import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["websocket"])


@router.websocket("/ws/vessels")
async def vessel_updates_ws(
    websocket: WebSocket,
    redis: RedisClient = Depends(get_redis),
) -> None:
    """Stream live vessel scoring updates to the browser map.

    The client receives a JSON array of vessel objects every time the Celery
    worker completes a scoring cycle (approximately every 5 minutes).

    Protocol: server sends JSON text frames; client is read-only.
    """
    await websocket.accept()
    log.info("ws.vessel_updates.connected", client=str(websocket.client))

    try:
        async for message in redis.subscribe(VESSEL_UPDATES_CHANNEL):
            try:
                await websocket.send_text(message)
            except WebSocketDisconnect:
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("ws.send_error", error=str(exc))
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # noqa: BLE001
        log.warning("ws.vessel_updates.error", error=str(exc))
    finally:
        log.info("ws.vessel_updates.disconnected", client=str(websocket.client))
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
