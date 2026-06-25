"""Open-Meteo Marine API — batch weather + ocean conditions per H3 cell.

Free, no API key, no rate limit (fair use).
Docs: https://open-meteo.com/en/docs/marine-weather-api

We use the "current" endpoint (not hourly forecast) to get the single
most recent value per cell center. Batch via asyncio.gather.
"""

from __future__ import annotations

import asyncio
from typing import Any

import h3 as _h3
import httpx

BASE = "https://marine-api.open-meteo.com/v1/marine"
TIMEOUT = 12.0

CURRENT_VARS = ",".join([
    "wave_height",
    "wave_period",
    "wave_direction",
    "ocean_current_velocity",
    "ocean_current_direction",
])

# Add surface temperature via main Open-Meteo (marine API doesn't have SST)
SST_BASE = "https://api.open-meteo.com/v1/forecast"
SST_VAR = "sea_surface_temperature"


async def _fetch_one(
    client: httpx.AsyncClient, cell_id: str
) -> dict[str, Any]:
    """Fetch marine conditions for the centre of one H3 cell."""
    lat, lon = _h3.cell_to_latlng(cell_id)

    try:
        r = await client.get(BASE, params={
            "latitude":  round(lat, 4),
            "longitude": round(lon, 4),
            "current":   CURRENT_VARS,
            "timezone":  "UTC",
        })
        if r.status_code != 200:
            return {}
        raw = r.json()
    except Exception:
        return {}

    cur = raw.get("current") or raw.get("current_weather") or {}
    units = raw.get("current_units", {})

    # SST from the main API
    sst = None
    try:
        r2 = await client.get(SST_BASE, params={
            "latitude":  round(lat, 4),
            "longitude": round(lon, 4),
            "current":   SST_VAR,
            "timezone":  "UTC",
        })
        if r2.status_code == 200:
            sst_data = r2.json().get("current", {})
            sst = sst_data.get(SST_VAR)
    except Exception:
        pass

    return {
        "wave_height_m":          cur.get("wave_height"),
        "wave_period_s":          cur.get("wave_period"),
        "wave_direction_deg":     cur.get("wave_direction"),
        "current_velocity_ms":    cur.get("ocean_current_velocity"),
        "current_direction_deg":  cur.get("ocean_current_direction"),
        "sea_surface_temp_c":     sst,
    }


async def fetch_marine_conditions(
    cell_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return {cell_id: marine_conditions} for a list of H3 cells.

    Runs all requests concurrently. Cells with errors return {}.
    """
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            *[_fetch_one(client, cid) for cid in cell_ids],
            return_exceptions=True,
        )

    return {
        cid: (r if isinstance(r, dict) else {})
        for cid, r in zip(cell_ids, results)
    }
