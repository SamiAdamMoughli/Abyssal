"""GFW enrichment — vessel identity search + 90-day event history per MMSI.

Two separate GFW API calls:
  1. GET /v3/vessels/search — identity: IMO, flag, length, tonnage, vessel type
  2. POST /events           — 90-day anomaly history for this specific vessel

Both are optional: if the GFW token is missing or the API is down, we return
partial data rather than failing the whole detail response.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

GFW_BASE = "https://gateway.api.globalfishingwatch.org/v3"
VESSEL_IDENTITY_DATASET = "public-global-vessel-identity:latest"
EVENT_DATASETS = [
    "public-global-fishing-events:latest",
    "public-global-gaps-events:latest",
    "public-global-loitering-events:latest",
    "public-global-encounters-events:latest",
    "public-global-port-visits-c2-events:latest",
]
HISTORY_DAYS = 90
TIMEOUT = 20.0


def _headers() -> dict[str, str]:
    token = os.environ.get("GFW_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def fetch_identity(mmsi: str) -> dict[str, Any]:
    """Return vessel identity fields from the GFW vessel registry."""
    params = {
        "where": f"ssvid='{mmsi}'",
        "datasets[0]": VESSEL_IDENTITY_DATASET,
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(
                f"{GFW_BASE}/vessels/search",
                headers=_headers(),
                params=params,
            )
            if r.status_code != 200:
                return {}
            data = r.json()
    except Exception:
        return {}

    entries = data.get("entries", [])
    if not entries:
        return {}

    v = entries[0]
    reg = (v.get("registryInfo") or [{}])[0]
    self_rep = (v.get("selfReportedInfo") or [{}])[0]

    return {
        "gfw_id":     v.get("id"),
        "imo":        reg.get("imoNumber") or self_rep.get("imo"),
        "flag":       reg.get("flag") or self_rep.get("flag"),
        "vessel_type": reg.get("vesselType") or self_rep.get("vesselType"),
        "length_m":   reg.get("lengthM") or reg.get("length"),
        "tonnage_gt": reg.get("tonnageGt") or reg.get("grossTonnage"),
        "owner":      reg.get("owner") or reg.get("registeredOwner"),
        "callsign":   reg.get("callsign") or self_rep.get("callsign"),
        "built_year": reg.get("builtYear"),
    }


async def fetch_event_history(mmsi: str) -> dict[str, Any]:
    """Return 90-day anomaly summary: loitering, gaps, encounters, port visits."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    body = {
        "datasets": EVENT_DATASETS,
        "startDate": start,
        "endDate": end,
        "vessels": [{"ssvid": mmsi}],
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{GFW_BASE}/events",
                headers=_headers(),
                json=body,
                params={"limit": 200, "offset": 0},
            )
            if r.status_code not in (200, 201):
                return {}
            data = r.json()
    except Exception:
        return {}

    events = data.get("entries", [])
    loitering, gaps, encounters, port_visits = [], [], [], []
    last_encounter_mmsi = None

    for ev in events:
        t = str(ev.get("type", "")).lower()
        if t == "loitering":
            loit = ev.get("loitering") or {}
            loitering.append({
                "start": ev.get("start"),
                "end":   ev.get("end"),
                "hours": loit.get("totalTimeHours"),
                "lat":   (ev.get("position") or {}).get("lat"),
                "lon":   (ev.get("position") or {}).get("lon"),
            })
        elif t == "gap":
            gap = ev.get("gap") or {}
            gaps.append({
                "start": ev.get("start"),
                "end":   ev.get("end"),
                "hours": gap.get("durationHours"),
            })
        elif t == "encounter":
            enc = ev.get("encounter") or {}
            other = enc.get("vessel") or enc.get("otherVessel") or {}
            other_mmsi = str(other.get("ssvid") or other.get("mmsi") or "")
            if other_mmsi:
                last_encounter_mmsi = other_mmsi
            encounters.append({
                "start":       ev.get("start"),
                "end":         ev.get("end"),
                "hours":       enc.get("durationHours"),
                "other_mmsi":  other_mmsi,
                "other_flag":  other.get("flag"),
            })
        elif t in ("port_visit", "port-visit"):
            visit = ev.get("portVisit") or ev.get("port_visit") or {}
            port_visits.append({
                "start":     ev.get("start"),
                "end":       ev.get("end"),
                "port_name": visit.get("name") or visit.get("portName"),
                "country":   visit.get("flag") or visit.get("country"),
            })

    return {
        "loitering_events_90d": len(loitering),
        "gap_events_90d":       len(gaps),
        "encounter_events_90d": len(encounters),
        "port_visits_90d":      port_visits[-5:],   # last 5 port calls
        "last_encounter_mmsi":  last_encounter_mmsi,
        "loitering_detail":     loitering[-3:],      # last 3 loitering events
        "gap_detail":           gaps[-3:],
    }
