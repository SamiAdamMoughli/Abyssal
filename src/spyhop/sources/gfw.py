"""Fetch recent vessel positions from the Global Fishing Watch API v3.

Pulls the last 24 h of fishing, loitering, encounter and gap events.
Each event carries a lat/lon position; we deduplicate by MMSI and keep
the most recent position for each vessel.

Returns a list of plain dicts compatible with _upsert_vessels_sync().

GFW events reference:
  https://globalfishingwatch.org/our-apis/documentation#events
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

GFW_BASE = "https://gateway.api.globalfishingwatch.org/v3"

EVENT_DATASETS = [
    "public-global-fishing-events:latest",
    "public-global-loitering-events:latest",
    "public-global-encounters-events:latest",
    "public-global-gaps-events:latest",
    "public-global-port-visits-c2-events:latest",
]

# GFW vessel type → our schema vessel_type
_TYPE_MAP: dict[str, str] = {
    "FISHING":             "fishing",
    "CARRIER":             "cargo",
    "SUPPORT":             "cargo",
    "CARGO":               "cargo",
    "TANKER":              "tanker",
    "PASSENGER":           "passenger",
    "SEISMIC_VESSEL":      "research",
    "RESEARCH":            "research",
    "PATROL_VESSEL":       "enforcement",
    "BUNKER_OR_TANKER":    "tanker",
    "REFRIGERATED_CARGO":  "cargo",
    "CONTAINER":           "container",
    "BULK_CARRIER":        "bulk_carrier",
}


def _api_key() -> str:
    return os.environ.get("GFW_API_KEY", "") or os.environ.get("GFW_API_TOKEN", "")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }


def fetch_recent_vessels(
    hours: int = 24,
    limit: int = 1000,
    timeout: float = 60.0,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` unique vessels active in the last ``hours`` hours.

    Makes a single POST to /v3/events with all relevant dataset types, then
    deduplicates by MMSI, keeping the most-recent-event position.
    """
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "datasets": EVENT_DATASETS,
        "startDate": start,
        "endDate":   end,
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(
            f"{GFW_BASE}/events",
            headers=_headers(),
            json=payload,
            params={"limit": limit, "offset": 0},
        )
        resp.raise_for_status()
        data = resp.json()

    events: list[dict] = data.get("entries", [])

    # --- Deduplicate by MMSI, keeping the most-recent event -----------------
    latest: dict[str, dict] = {}
    for ev in events:
        vessel_info = ev.get("vessel") or {}
        mmsi = str(vessel_info.get("ssvid") or vessel_info.get("mmsi") or "").strip()
        if not mmsi or mmsi == "0":
            continue

        ev_end = ev.get("end") or ev.get("start") or ""
        prev = latest.get(mmsi)
        if prev is None or ev_end > prev.get("_ev_end", ""):
            pos = ev.get("position") or {}
            latest[mmsi] = {
                "_ev_end":   ev_end,
                "_ev_type":  str(ev.get("type", "")).lower(),
                "mmsi":      mmsi,
                "name":      vessel_info.get("name") or f"VESSEL-{mmsi[-4:]}",
                "flag":      vessel_info.get("flag") or "UNK",
                "vessel_type": _TYPE_MAP.get(
                    str(vessel_info.get("vesselType") or "").upper(), "fishing"
                ),
                "lat":       float(pos.get("lat") or 0.0),
                "lon":       float(pos.get("lon") or 0.0),
                "speed_knots": 0.0,
            }

    # --- Drop vessels with no usable position --------------------------------
    results = [
        v for v in latest.values()
        if v["lat"] != 0.0 or v["lon"] != 0.0
    ]

    return results
