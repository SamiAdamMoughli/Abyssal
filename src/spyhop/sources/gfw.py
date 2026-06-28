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
    return (
        os.environ.get("GFW_API_KEY", "")
        or os.environ.get("GFW_API_TOKEN", "")
    )


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_api_key()}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }


def fetch_recent_vessels(
    hours: int = 168,
    limit: int = 10000,
    timeout: float = 60.0,
    page_size: int = 2000,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` unique vessels active in the last ``hours`` h.

    Paginates /v3/events in pages of ``page_size`` until ``limit`` events
    are collected or the API is exhausted, then deduplicates by MMSI.
    """
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {"datasets": EVENT_DATASETS, "startDate": start, "endDate": end}

    all_events: list[dict] = []
    offset = 0
    with httpx.Client(timeout=timeout) as client:
        while len(all_events) < limit:
            fetch = min(page_size, limit - len(all_events))
            resp = client.post(
                f"{GFW_BASE}/events",
                headers=_headers(),
                json=payload,
                params={"limit": fetch, "offset": offset},
            )
            resp.raise_for_status()
            data = resp.json()
            entries = data.get("entries", [])
            if not entries:
                break
            all_events.extend(entries)
            offset += len(entries)
            if offset >= data.get("total", 0):
                break

    return _dedup_events(all_events)


def _dedup_events(events: list[dict]) -> list[dict[str, Any]]:
    """Deduplicate GFW events by MMSI, keeping the most-recent position."""
    latest: dict[str, dict] = {}
    for ev in events:
        info = ev.get("vessel") or {}
        mmsi = str(
            info.get("ssvid") or info.get("mmsi") or ""
        ).strip()
        if not mmsi or mmsi == "0":
            continue
        ev_end = ev.get("end") or ev.get("start") or ""
        prev = latest.get(mmsi)
        if prev is not None and ev_end <= prev.get("_ev_end", ""):
            continue
        pos = ev.get("position") or {}
        vtype = _TYPE_MAP.get(
            str(info.get("vesselType") or "").upper(), "fishing"
        )
        latest[mmsi] = {
            "_ev_end":    ev_end,
            "_ev_type":   str(ev.get("type", "")).lower(),
            "mmsi":       mmsi,
            "name":       info.get("name") or f"VESSEL-{mmsi[-4:]}",
            "flag":       info.get("flag") or "UNK",
            "vessel_type": vtype,
            "lat":        float(pos.get("lat") or 0.0),
            "lon":        float(pos.get("lon") or 0.0),
            "speed_knots": 0.0,
        }
    return [v for v in latest.values() if v["lat"] or v["lon"]]
