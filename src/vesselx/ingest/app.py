"""Management API for the VesselX ingestion service.

The ingestion worker itself is headless. This API is intentionally small and is
useful for orchestration probes, source visibility, and future subscription
control.
"""

from __future__ import annotations

from fastapi import FastAPI

from spyhop.config import get_settings
from vesselx import __version__

settings = get_settings()

app = FastAPI(
    title="VesselX Ingestion Engine & Sensor Gateway",
    version=__version__,
    description=(
        "Headless ingestion management plane for AIS, NMEA, SAR, and VIIRS "
        "source adapters."
    ),
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "vesselx-ingest",
        "version": __version__,
    }


@app.get("/sources")
async def sources() -> dict[str, object]:
    return {
        "service": "vesselx-ingest",
        "active": {
            "data_source": settings.DATA_SOURCE,
            "aisstream": bool(settings.AISSTREAM_API_KEY),
            "gfw": bool(settings.GFW_API_TOKEN),
        },
        "adapters": [
            {
                "name": "aisstream",
                "kind": "websocket",
                "status": "configured"
                if settings.AISSTREAM_API_KEY else "missing_api_key",
            },
            {
                "name": "gfw",
                "kind": "scheduled_api",
                "status": "configured"
                if settings.GFW_API_TOKEN else "missing_api_key",
            },
            {
                "name": "nmea0183",
                "kind": "serial_usb",
                "status": "planned",
            },
            {
                "name": "sentinel_sar",
                "kind": "scheduled_satellite_pass",
                "status": "planned",
            },
            {
                "name": "noaa_viirs",
                "kind": "scheduled_satellite_pass",
                "status": "planned",
            },
        ],
    }
