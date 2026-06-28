"""VesselX Gateway Service — telemetry ingestion and sensor entry point.

Manages two parallel ingestion paths:

  1. HTTP webhooks  — satellite AIS push from Spire Maritime / Orbcomm.
                      Mounted at /webhooks/{spire,orbcomm,generic}.
  2. NMEA TCP server — local VHF hardware via serial-to-TCP bridge.
                      Listens on port 10110 (IEC 61162-450) in the background.

Both paths normalise records and publish them to the
``vesselx:telemetry:raw`` Redis Stream where the spatial worker picks them up.

Environment variables:
  NMEA_TCP_HOST          — bind address for the NMEA server (default: 0.0.0.0)
  NMEA_TCP_PORT          — port for the NMEA server (default: 10110)
  SPIRE_WEBHOOK_SECRET   — HMAC-SHA256 secret for Spire push validation
  ORBCOMM_WEBHOOK_SECRET — HMAC-SHA256 secret for Orbcomm push validation
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from spyhop.config import get_settings
from vesselx import __version__
from vesselx.gateway.nmea import NMEATCPServer
from vesselx.gateway.publisher import (
    STREAM_RAW,
    close as close_publisher,
    connectivity_watchdog,
    is_online,
)
from vesselx.gateway.webhook import router as webhook_router

settings = get_settings()
log = logging.getLogger(__name__)

_NMEA_HOST = os.getenv("NMEA_TCP_HOST", "0.0.0.0")
_NMEA_PORT = int(os.getenv("NMEA_TCP_PORT", "10110"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log.info("vesselx.gateway.starting version=%s", __version__)

    nmea_server  = NMEATCPServer(host=_NMEA_HOST, port=_NMEA_PORT)
    nmea_task    = asyncio.create_task(nmea_server.start())
    watchdog_task = asyncio.create_task(connectivity_watchdog())

    log.info(
        "vesselx.gateway.ready nmea=%s:%d stream=%s",
        _NMEA_HOST, _NMEA_PORT, STREAM_RAW,
    )
    yield

    for task in (nmea_task, watchdog_task):
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    await close_publisher()
    log.info("vesselx.gateway.stopped")


app = FastAPI(
    title="VesselX Ingestion Engine & Sensor Gateway",
    version=__version__,
    description=(
        "Multi-source telemetry ingestion: satellite AIS webhooks (Spire, "
        "Orbcomm) and local NMEA 0183 TCP/VHF hardware. Publishes normalised "
        "payloads to the vesselx:telemetry:raw Redis Stream."
    ),
    lifespan=lifespan,
)

app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "vesselx-gateway", "version": __version__}


@app.get("/connectivity")
async def connectivity() -> dict[str, object]:
    """Report current link state based on the connectivity watchdog heartbeat."""
    online = await is_online()
    return {
        "online": online,
        "mode": "satellite+serial" if online else "serial_only",
        "note": (
            "All adapters active."
            if online
            else "Satellite link unavailable — VHF/NMEA serial ingestion continues."
        ),
    }


@app.get("/sources")
async def sources() -> dict[str, object]:
    return {
        "service": "vesselx-gateway",
        "version": __version__,
        "stream":  STREAM_RAW,
        "adapters": [
            {
                "name":     "aisstream",
                "kind":     "websocket",
                "status":   "configured" if settings.AISSTREAM_API_KEY else "missing_api_key",
            },
            {
                "name":     "spire",
                "kind":     "satellite_webhook",
                "endpoint": "/webhooks/spire",
                "status":   "verified" if os.getenv("SPIRE_WEBHOOK_SECRET") else "no_secret",
            },
            {
                "name":     "orbcomm",
                "kind":     "satellite_webhook",
                "endpoint": "/webhooks/orbcomm",
                "status":   "verified" if os.getenv("ORBCOMM_WEBHOOK_SECRET") else "no_secret",
            },
            {
                "name":   "nmea0183",
                "kind":   "serial_tcp",
                "host":   _NMEA_HOST,
                "port":   _NMEA_PORT,
                "status": "active",
            },
        ],
    }
