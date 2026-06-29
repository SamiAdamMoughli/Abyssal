"""Vessel data fusion with cascading fallback strategy.

Identity fields follow a five-tier priority chain:

  P1  GFW Vessel API        — structured registry from 30+ national sources
  P2  Equasis               — EMSA-managed static data + PSC inspections
  P3  Wikidata SPARQL       — community-curated, covers major named vessels
  P4  ITU Ship Station List — MMSI / callsign / flag (official ITU record)
  P5  EU Vessel Register    — EU fishing fleet (CFR-keyed, EU flags only)
  P6  Canadian NMID         — Transport Canada (MMSI 316xxx only)
  P7  DB record             — already scored by our own pipeline (always present)

Live anomaly history and port geocoding run in parallel (no fallback needed —
they either have data or they don't).

Cache strategy (set by the caller in detail.py):
  - Identity fields: 30-day TTL  (flag/type/IMO rarely change)
  - Events history:  1-hour TTL  (loitering/encounters change daily)
"""

from __future__ import annotations

import asyncio
from typing import Any

from .canadian_nmid import fetch_identity as fetch_canadian
from .equasis import fetch_identity as fetch_equasis
from .eu_register import fetch_identity as fetch_eu_register
from .gfw import fetch_event_history, fetch_identity
from .itu import fetch_identity as fetch_itu
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
    """Cascading identity resolution across all configured registries.

    Priority order (highest first):
      GFW → Equasis → Wikidata → ITU → EU Register → Canadian NMID → DB

    P1 (GFW) and P3 (Wikidata) fire in parallel with the other tier-2 sources
    to minimise wall-clock latency.  Equasis, ITU, and registry sources also
    run in parallel.  Results are merged with higher-priority sources winning.
    """
    imo_hint = db_vessel.get("imo")

    # Tier 1 + tier 3 fire together (both need IMO hint)
    raw = await asyncio.gather(
        fetch_identity(mmsi),                          # GFW
        fetch_equasis(mmsi=mmsi, imo=imo_hint),        # Equasis
        fetch_by_imo(imo_hint),                        # Wikidata
        fetch_itu(mmsi),                               # ITU Ship Station List
        fetch_eu_register(mmsi=mmsi),                  # EU Vessel Register
        fetch_canadian(mmsi),                          # Canadian NMID
        return_exceptions=True,
    )
    gfw: dict[str, Any] = raw[0] if isinstance(raw[0], dict) else {}
    equasis: dict[str, Any] = raw[1] if isinstance(raw[1], dict) else {}
    wiki: dict[str, Any] = raw[2] if isinstance(raw[2], dict) else {}
    itu: dict[str, Any] = raw[3] if isinstance(raw[3], dict) else {}
    eu_reg: dict[str, Any] = raw[4] if isinstance(raw[4], dict) else {}
    canadian: dict[str, Any] = raw[5] if isinstance(raw[5], dict) else {}

    # Re-query Wikidata if GFW resolved a better IMO than the DB record
    gfw_imo = gfw.get("imo")
    if gfw_imo and gfw_imo != imo_hint:
        try:
            wiki = await fetch_by_imo(gfw_imo)
        except Exception:
            pass

    # Resolved IMO — best available across all sources
    resolved_imo = _first(
        gfw.get("imo"),
        equasis.get("imo"),
        imo_hint,
    )

    source_map = [
        ("gfw", gfw),
        ("equasis", equasis),
        ("wikidata", wiki),
        ("itu", itu),
        ("eu_vessel_register", eu_reg),
        ("canadian_nmid", canadian),
    ]
    active_sources = [s for s, d in source_map if d]

    return {
        "gfw_id": _first(gfw.get("gfw_id")),
        "imo": resolved_imo,
        "flag": _first(
            gfw.get("flag"),
            equasis.get("flag"),
            itu.get("flag"),
            eu_reg.get("flag"),
            canadian.get("flag"),
            wiki.get("flag"),
            db_vessel.get("flag"),
        ),
        "vessel_type": _first(
            gfw.get("vessel_type"),
            equasis.get("vessel_type"),
            wiki.get("vessel_type"),
            canadian.get("vessel_type"),
            db_vessel.get("vessel_type"),
        ),
        "length_m": _first(
            gfw.get("length_m"),
            eu_reg.get("length_m"),
            canadian.get("length_m"),
        ),
        "tonnage_gt": _first(
            gfw.get("tonnage_gt"),
            equasis.get("tonnage_gt"),
            eu_reg.get("tonnage_gt"),
            canadian.get("tonnage_gt"),
        ),
        "owner": _first(
            gfw.get("owner"),
            equasis.get("owner"),
            canadian.get("owner"),
        ),
        "callsign": _first(
            gfw.get("callsign"),
            itu.get("callsign"),
            equasis.get("callsign"),
        ),
        "built_year": _first(
            gfw.get("built_year"),
            equasis.get("built_year"),
            canadian.get("built_year"),
            wiki.get("built_year"),
        ),
        "image_url": _first(wiki.get("image_url")),
        # Equasis-only fields surfaced into the identity block
        "class_society": equasis.get("class_society"),
        "manager": equasis.get("manager"),
        "psc_deficiencies_5y": equasis.get("psc_deficiencies_5y"),
        "psc_detentions_5y": equasis.get("psc_detentions_5y"),
        # EU fishing vessel fields
        "cfr": eu_reg.get("cfr"),
        "gear_codes": eu_reg.get("gear_codes"),
        "licence_active": eu_reg.get("licence_active"),
        # ITU fields
        "itu_administration": itu.get("administration"),
        # Canadian NMID fields
        "port_of_registry": canadian.get("port_of_registry"),
        "official_number": canadian.get("official_number"),
        "_sources": active_sources,
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
            "manager": identity.get("manager"),
            "callsign": identity.get("callsign"),
            "built_year": identity.get("built_year"),
            "image_url": identity.get("image_url"),
            "gfw_id": identity.get("gfw_id"),
            "class_society": identity.get("class_society"),
            "psc_deficiencies_5y": identity.get("psc_deficiencies_5y"),
            "psc_detentions_5y": identity.get("psc_detentions_5y"),
            "cfr": identity.get("cfr"),
            "gear_codes": identity.get("gear_codes"),
            "licence_active": identity.get("licence_active"),
            "itu_administration": identity.get("itu_administration"),
            "port_of_registry": identity.get("port_of_registry"),
            "official_number": identity.get("official_number"),
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
        "gfw_registry": {
            "geartype": vessel.get("gfw_geartype"),
            "flag": vessel.get("gfw_flag"),
            "length_m": vessel.get("gfw_length_m"),
            "engine_kw": vessel.get("gfw_engine_kw"),
            "tonnage_gt": vessel.get("gfw_tonnage_gt"),
            "fishing_hours": vessel.get("gfw_fishing_hours"),
            "active_hours": vessel.get("gfw_active_hours"),
            "registries": vessel.get("gfw_registries"),
            "self_reported_fishing": vessel.get(
                "gfw_self_reported_fishing"
            ),
        },
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
