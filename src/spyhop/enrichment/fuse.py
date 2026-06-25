"""Vessel data fusion with cascading fallback strategy.

Identity fields follow a three-tier priority chain:

  P1  GFW Vessel API   — structured registry from 30+ national sources
  P2  Wikidata SPARQL  — community-curated, covers major named vessels
  P3  DB record        — already scored by our own pipeline (always present)

Live anomaly history and port geocoding run in parallel (no fallback needed —
they either have data or they don't).

Cache strategy (set by the caller in detail.py):
  - Identity fields: 30-day TTL  (flag/type/IMO rarely change)
  - Events history:  1-hour TTL  (loitering/encounters change daily)
"""

from __future__ import annotations

import asyncio
from typing import Any

from .gfw import fetch_identity, fetch_event_history
from .ports import geocode_destination
from .wikidata import fetch_by_imo


def _first(*values: Any) -> Any:
    """Return the first non-None, non-empty value."""
    for v in values:
        if v is not None and v != "" and v != "unknown" and v != "Unknown":
            return v
    return None


async def _resolve_identity(
    mmsi: str,
    db_vessel: dict[str, Any],
) -> dict[str, Any]:
    """Cascading identity resolution: GFW → Wikidata → DB.

    Fires GFW and Wikidata in parallel, then merges with GFW taking
    priority. If GFW returns a different IMO than the DB record, we
    re-query Wikidata with the corrected IMO.
    """
    imo_hint = db_vessel.get("imo")

    gfw, wiki = await asyncio.gather(
        fetch_identity(mmsi),
        fetch_by_imo(imo_hint),
        return_exceptions=True,
    )
    if isinstance(gfw, Exception):
        gfw = {}
    if isinstance(wiki, Exception):
        wiki = {}

    # Re-query Wikidata if GFW resolved a better IMO
    gfw_imo = gfw.get("imo") if isinstance(gfw, dict) else None
    if gfw_imo and gfw_imo != imo_hint:
        try:
            wiki = await fetch_by_imo(gfw_imo)
        except Exception:
            pass

    sources = [
        s for s, d in [("gfw", gfw), ("wikidata", wiki)]
        if isinstance(d, dict) and d
    ]

    return {
        "gfw_id": _first(gfw.get("gfw_id")),
        "imo": _first(gfw.get("imo"), imo_hint),
        "flag": _first(
            gfw.get("flag"),
            wiki.get("flag"),
            db_vessel.get("flag"),
        ),
        "vessel_type": _first(
            gfw.get("vessel_type"),
            wiki.get("vessel_type"),
            db_vessel.get("vessel_type"),
        ),
        "length_m": _first(gfw.get("length_m")),
        "tonnage_gt": _first(gfw.get("tonnage_gt")),
        "owner": _first(gfw.get("owner")),
        "callsign": _first(gfw.get("callsign")),
        "built_year": _first(gfw.get("built_year"), wiki.get("built_year")),
        "image_url": _first(wiki.get("image_url")),
        "_sources": sources,
    }


async def enrich(vessel: dict[str, Any]) -> dict[str, Any]:
    """Build the fused vessel super-object.

    `vessel` is VesselPosition.to_dict() from the DB.
    Returns a dict suitable for JSON serialisation.
    """
    mmsi = str(vessel.get("mmsi", ""))
    name = vessel.get("name") or "UNKNOWN"
    destination_raw = vessel.get("destination") or ""

    identity, history, dest_coords = await asyncio.gather(
        _resolve_identity(mmsi, vessel),
        fetch_event_history(mmsi),
        geocode_destination(destination_raw),
        return_exceptions=True,
    )
    if isinstance(identity, Exception):
        identity = {}
    if isinstance(history, Exception):
        history = {}
    if isinstance(dest_coords, Exception):
        dest_coords = None

    sanction_status, iuu_status = _check_watchlists(
        mmsi=mmsi,
        imo=identity.get("imo"),
        name=name,
    )

    return {
        "mmsi": mmsi,
        "imo": identity.get("imo"),
        "name": name,
        "identity": {
            "flag": identity.get("flag"),
            "type": identity.get("vessel_type"),
            "length_m": identity.get("length_m"),
            "tonnage_gt": identity.get("tonnage_gt"),
            "owner": identity.get("owner"),
            "callsign": identity.get("callsign"),
            "built_year": identity.get("built_year"),
            "image_url": identity.get("image_url"),
            "gfw_id": identity.get("gfw_id"),
            "_sources": identity.get("_sources", []),
        },
        "live_navigation": {
            "lat": vessel.get("lat"),
            "lon": vessel.get("lon"),
            "speed_knots": vessel.get("speed_knots"),
            "heading": vessel.get("cog_degrees"),
            "destination_raw": destination_raw or None,
            "destination_coords": dest_coords,
            "ais_vessel_class": vessel.get("ais_vessel_class"),
            "days_since_port": vessel.get("days_since_port"),
            "distance_to_port_nm": vessel.get("distance_to_nearest_port_nm"),
        },
        "historical_anomalies": {
            "loitering_events_90d": history.get("loitering_events_90d", 0),
            "gap_events_90d": history.get("gap_events_90d", 0),
            "encounter_events_90d": history.get("encounter_events_90d", 0),
            "port_visits_90d": history.get("port_visits_90d", []),
            "last_encounter_mmsi": history.get("last_encounter_mmsi"),
            "loitering_detail": history.get("loitering_detail", []),
            "gap_detail": history.get("gap_detail", []),
            "ais_gap_hours_db": vessel.get("ais_gap_hours"),
            "loitering_hours_db": vessel.get("loitering_hours"),
            "rendezvous_hours_db": vessel.get("rendezvous_duration_hours"),
            "sanction_status": sanction_status,
            "iuu_status": iuu_status,
            "in_protected_area": vessel.get("in_protected_area"),
            "nearest_mpa_nm": vessel.get("nearest_mpa_nm"),
        },
        "calculated_risk_score": vessel.get("risk_score"),
        "top_reason": vessel.get("top_reason_label"),
        "reasons": vessel.get("reasons", []),
        "data_source": vessel.get("data_source"),
        "updated_at": vessel.get("updated_at"),
    }


def _check_watchlists(
    mmsi: str,
    imo: str | None,
    name: str,
) -> tuple[str, str]:
    sanction_status = "CLEAN"
    iuu_status = "CLEAN"
    try:
        from backend.app.sources import opensanctions
        hit = opensanctions.match_vessel(mmsi=mmsi, imo=imo, name=name)
        if hit:
            src = hit.get("source_label") or hit.get("match", "unknown list")
            sanction_status = f"MATCHED ({src})"
    except Exception:
        sanction_status = "UNAVAILABLE"
    try:
        from backend.app.sources import iuu_list
        hit = iuu_list.lookup(mmsi=mmsi, imo=imo, name=name)
        if hit:
            entry = hit.get("entry") or {}
            org = entry.get("rfmo") or entry.get("organization") or "RFMO"
            iuu_status = (
                f"MATCHED (listed by {org}, match: {hit.get('match')})"
            )
    except Exception:
        iuu_status = "UNAVAILABLE"
    return sanction_status, iuu_status
