"""Port geocoding — AIS destination text → coordinates + country via Nominatim.

Uses OSM Nominatim (free, no token). Rate limit: 1 req/s. We cache results
in a module-level dict so a session re-querying the same destination is free.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
TIMEOUT = 8.0
_cache: dict[str, dict[str, Any] | None] = {}

# strip common AIS filler tokens before geocoding
_STRIP = re.compile(
    r"\b(PORT|PT|ANCH|ANCHORAGE|TERMINAL|TML|CTR|"
    r"VIA|ETA|ETD|AT|TO|FOR|THE|OF)\b",
    re.IGNORECASE,
)


def _clean(raw: str) -> str:
    return _STRIP.sub("", raw).strip()


async def geocode_destination(raw: str) -> dict[str, Any] | None:
    """Return {lat, lon, display_name, country_code} or None if not found."""
    if not raw or not raw.strip():
        return None

    key = raw.strip().upper()
    if key in _cache:
        return _cache[key]

    query = _clean(key)
    if not query:
        _cache[key] = None
        return None

    params = {
        "q": query,
        "featuretype": "settlement",
        "format": "jsonv2",
        "limit": 1,
        "addressdetails": 1,
    }
    headers = {"User-Agent": "SpyhopMissionRadar/1.0 (sami@spyhop.dev)"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(NOMINATIM_URL, params=params, headers=headers)
            if r.status_code != 200:
                _cache[key] = None
                return None
            results = r.json()
    except Exception:
        _cache[key] = None
        return None

    if not results:
        _cache[key] = None
        return None

    hit = results[0]
    addr = hit.get("address", {})
    result = {
        "lat":          float(hit["lat"]),
        "lon":          float(hit["lon"]),
        "display_name": hit.get("display_name"),
        "country":      addr.get("country"),
        "country_code": addr.get("country_code", "").upper(),
    }
    _cache[key] = result
    return result
