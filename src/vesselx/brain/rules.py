"""Tactical rule catalogue for the VesselX brain evaluator.

Each Rule maps a vessel-state snapshot (a plain dict) to a boolean trigger
and a human-readable alert message. Rules are pure functions — stateless and
side-effect-free — so they can be unit-tested in isolation without a database
or Redis connection.

Rule predicates receive the vessel_state dict produced by the spatial worker
(after H3 enrichment) optionally augmented by the brain tasks with extra
signals (IUU blacklist flag, etc.).

Severity levels mirror GFW / Sea Shepherd alert triage conventions:
  INFO     — situational awareness; no immediate action required
  WARNING  — watch closely; possible rule violation
  ALERT    — likely violation; dispatch analyst
  CRITICAL — confirmed high-confidence violation; immediate response
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


def _species(v: dict[str, Any], key: str = "corridor_species") -> str:
    """Comma-joined species string, safe against None and plain strings."""
    raw = v.get(key) or []
    if isinstance(raw, str):
        raw = [raw]
    return ", ".join(raw) or "unknown species"


def _spawning(v: dict[str, Any]) -> str:
    return _species(v, "spawning_species")


class Severity(str, Enum):
    INFO     = "info"
    WARNING  = "warning"
    ALERT    = "alert"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Rule:
    id:        str
    label:     str
    severity:  Severity
    predicate: Callable[[dict[str, Any]], bool]
    message:   Callable[[dict[str, Any]], str]


# ---------------------------------------------------------------------------
# Flag-state risk tiers
# Based on RFMO IUU enforcement records, EU IUU carding history, and
# open-registry evasion patterns (ITF / Equasis / FAO data).
# ---------------------------------------------------------------------------

# CRITICAL — states with active RFMO IUU listings or EU red-card history
_FLAG_CRITICAL: frozenset[str] = frozenset({
    "PRK",  # North Korea — UNSC sanctions, widespread IUU
    "GIN",  # Guinea — repeat EU red card
    "SLE",  # Sierra Leone — recurring IUU flag-hopping hub
    "TOG",  # Togo — EU red card, low oversight
    "CMR",  # Cameroon — EU red card
    "BLZ",  # Belize — repeated RFMO IUU listings
    "KIR",  # Kiribati — WCPFC non-compliance
    "TUV",  # Tuvalu — open registry, low enforcement
    "COM",  # Comoros — flag of convenience, RFMO sanctions
})

# WARNING — open registries with elevated evasion risk
_FLAG_WARNING: frozenset[str] = frozenset({
    "PAN",  # Panama — largest open registry, used for evasion
    "LBR",  # Liberia — major flag of convenience
    "MHL",  # Marshall Islands — open registry
    "BHS",  # Bahamas — flag of convenience
    "ATG",  # Antigua & Barbuda — open registry
    "VCT",  # St Vincent & Grenadines — open registry
    "PLW",  # Palau — minimal enforcement
    "MDV",  # Maldives — limited RFMO oversight
    "HND",  # Honduras — open registry, low enforcement
    "BOL",  # Bolivia (landlocked but flags vessels) — minimal oversight
})

# INFO — states with known IUU fishing fleets but flag enforcement varies
_FLAG_INFO: frozenset[str] = frozenset({
    "CHN",  # China — largest distant-water fleet, frequent IUU incidents
    "VNM",  # Vietnam — serial EU yellow card for IUU
    "TWN",  # Taiwan — multiple RFMO violations
    "KOR",  # South Korea — CCAMLR and WCPFC violations
    "IDN",  # Indonesia — domestic IUU significant
    "PHL",  # Philippines — WCPFC compliance issues
    "SEN",  # Senegal — license violations in own waters
})


# ---------------------------------------------------------------------------
# Rule catalogue
# ---------------------------------------------------------------------------

RULES: list[Rule] = [

    # --- Zone rules ---------------------------------------------------------

    Rule(
        id="mpa_incursion",
        label="Marine Protected Area Incursion",
        severity=Severity.CRITICAL,
        predicate=lambda v: bool(v.get("in_protected_area")),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} detected inside a Marine Protected Area"
            f" (h3={v.get('h3_index')},"
            f" lat={v.get('lat') or 0:.4f},"
            f" lon={v.get('lon') or 0:.4f})."
        ),
    ),

    Rule(
        id="fishing_in_mpa",
        label="Suspected Fishing in Marine Protected Area",
        severity=Severity.CRITICAL,
        predicate=lambda v: (
            bool(v.get("in_protected_area"))
            and v.get("behavior_status") in ("trawling", "loitering")
        ),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} exhibiting"
            f" {v.get('behavior_status')} pattern inside MPA"
            f" (confidence={v.get('behavior_confidence', 0):.0%})."
        ),
    ),

    Rule(
        id="mpa_skirting",
        label="Marine Protected Area Border Skirting",
        severity=Severity.WARNING,
        predicate=lambda v: bool(v.get("border_skirting")),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} sustained near-boundary movement"
            f" outside MPA (nearest={v.get('nearest_mpa_nm', -1):.1f} nm)."
            f" Possible avoidance behaviour."
        ),
    ),

    Rule(
        id="extended_time_in_zone",
        label="Extended Dwell in Protected Zone",
        severity=Severity.ALERT,
        predicate=lambda v: float(v.get("time_in_zone_hours", 0)) > 4.0,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} has been inside a protected zone for"
            f" {v.get('time_in_zone_hours', 0):.1f} hours continuously."
        ),
    ),

    # --- Identity / AIS rules -----------------------------------------------

    Rule(
        id="ais_gap",
        label="Significant AIS Transmission Gap",
        severity=Severity.ALERT,
        predicate=lambda v: float(v.get("ais_gap_hours", 0)) > 2.0,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} AIS signal lost for"
            f" {v.get('ais_gap_hours', 0):.1f} hours"
            f" (gap_type={v.get('gap_type', 'unknown')})."
        ),
    ),

    Rule(
        id="spoofing_detected",
        label="AIS Position Spoofing Detected",
        severity=Severity.CRITICAL,
        predicate=lambda v: bool(v.get("spoofing_flag")),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} probable AIS position spoofing —"
            f" implied speed {v.get('spoofing_max_speed_kn', 0):.1f} kn"
            f" exceeds physical limits."
        ),
    ),

    Rule(
        id="iuu_blacklist",
        label="Vessel on IUU Blacklist",
        severity=Severity.CRITICAL,
        predicate=lambda v: bool(v.get("on_iuu_blacklist")),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} matches an entry on the IUU"
            f" vessel blacklist."
        ),
    ),

    # --- Behavioural rules --------------------------------------------------

    Rule(
        id="loitering_open_ocean",
        label="Loitering Behaviour (Open Ocean)",
        severity=Severity.WARNING,
        predicate=lambda v: (
            v.get("behavior_status") == "loitering"
            and float(v.get("behavior_confidence", 0)) >= 0.7
            and not v.get("in_protected_area")
        ),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} loitering in open ocean —"
            f" possible gear deployment or rendezvous"
            f" (confidence={v.get('behavior_confidence', 0):.0%})."
        ),
    ),

    Rule(
        id="rendezvous_transship_risk",
        label="Vessel Rendezvous — Transhipment Risk",
        severity=Severity.ALERT,
        predicate=lambda v: (
            v.get("rendezvous_meeting_class") == "transship_risk"
        ),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} in close proximity to a"
            f" {v.get('rendezvous_partner_type', 'unknown')} vessel for"
            f" {v.get('rendezvous_duration_hours', 0):.1f} h —"
            f" possible transhipment."
        ),
    ),

    Rule(
        id="dark_vessel_candidate",
        label="Dark Vessel (No Matching AIS) Candidate",
        severity=Severity.WARNING,
        predicate=lambda v: bool(v.get("is_dark_candidate")),
        message=lambda v: (
            f"SAR/radar contact at"
            f" ({v.get('lat', 0):.4f}, {v.get('lon', 0):.4f})"
            f" with no matching AIS transponder signal."
        ),
    ),

    # --- Ecological rules ---------------------------------------------------
    # Fields injected by the spatial worker ecological enrichment pass:
    #   in_cetacean_corridor, corridor_species, corridor_season_peak,
    #   endangerment_weight, in_spawning_ground, spawning_species,
    #   whale_strike_risk  (scalar 0–1)

    Rule(
        id="cetacean_corridor_high_speed",
        label="High-Speed Transit Through Active Cetacean Corridor",
        severity=Severity.CRITICAL,
        predicate=lambda v: (
            bool(v.get("in_cetacean_corridor"))
            and float(v.get("sog", 0.0)) >= 10.0
        ),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} travelling at"
            f" {v.get('sog', 0):.1f} kn through an active cetacean"
            f" corridor ({_species(v)}) —"
            f" whale strike risk {v.get('whale_strike_risk', 0):.0%}."
            f" IWC/NOAA threshold is 10 kn."
        ),
    ),

    Rule(
        id="cetacean_corridor_transit",
        label="Vessel Transit Through Active Cetacean Corridor",
        severity=Severity.WARNING,
        predicate=lambda v: (
            bool(v.get("in_cetacean_corridor"))
            and 5.0 <= float(v.get("sog", 0.0)) < 10.0
            and float(v.get("corridor_season_peak", 0.0)) >= 0.3
        ),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} transiting active cetacean corridor"
            f" at {v.get('sog', 0):.1f} kn —"
            f" migration intensity {v.get('corridor_season_peak', 0):.0%}"
            f" ({_species(v)})."
        ),
    ),

    Rule(
        id="large_vessel_peak_corridor",
        label="Large Vessel in Peak-Season Cetacean Corridor",
        severity=Severity.ALERT,
        predicate=lambda v: (
            bool(v.get("in_cetacean_corridor"))
            and v.get("vessel_type", "").lower() in (
                "cargo", "tanker", "container"
            )
            and float(v.get("corridor_season_peak", 0.0)) >= 0.6
        ),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} ({v.get('vessel_type')}) in"
            f" peak-season cetacean corridor"
            f" (intensity {v.get('corridor_season_peak', 0):.0%}) —"
            f" vessels >5 000 DWT have no effective collision avoidance."
        ),
    ),

    Rule(
        id="fishing_in_spawning_ground",
        label="Suspected Fishing Activity in Active Spawning Ground",
        severity=Severity.CRITICAL,
        predicate=lambda v: (
            bool(v.get("in_spawning_ground"))
            and v.get("behavior_status") in ("trawling", "loitering")
            and float(v.get("behavior_confidence", 0.0)) >= 0.6
        ),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} exhibiting"
            f" {v.get('behavior_status')} pattern inside an active"
            f" spawning ground ({_spawning(v)}) —"
            f" bottom contact during broadcast spawning destroys"
            f" pelagic egg clouds."
        ),
    ),

    Rule(
        id="slow_vessel_in_spawning_ground",
        label="Stationary or Slow Vessel in Active Spawning Ground",
        severity=Severity.ALERT,
        predicate=lambda v: (
            bool(v.get("in_spawning_ground"))
            and float(v.get("sog", 99.0)) < 1.5
        ),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} stationary or near-stationary"
            f" (SOG {v.get('sog', 0):.1f} kn) inside an active spawning"
            f" ground — anchor chain and prop wash damage fertilised"
            f" surface egg concentrations."
        ),
    ),

    Rule(
        id="ecological_risk_composite",
        label="Elevated Composite Ecological Risk Score",
        severity=Severity.ALERT,
        predicate=lambda v: float(v.get("whale_strike_risk", 0.0)) >= 0.65,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} composite whale-strike risk"
            f" {v.get('whale_strike_risk', 0):.0%} —"
            f" SOG {v.get('sog', 0):.1f} kn"
            f" × season intensity {v.get('corridor_season_peak', 0):.0%}"
            f" × endangerment weight"
            f" {v.get('endangerment_weight', 0):.2f}."
        ),
    ),

    # --- Flag-state risk rules ----------------------------------------------

    Rule(
        id="flag_state_critical",
        label="Critical-Risk Flag State",
        severity=Severity.CRITICAL,
        predicate=lambda v: str(v.get("flag", "")).upper() in _FLAG_CRITICAL,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} flagged to {v.get('flag')} — "
            f"active RFMO IUU listings or EU red-card enforcement history."
        ),
    ),

    Rule(
        id="flag_state_warning",
        label="High-Risk Open Registry Flag",
        severity=Severity.WARNING,
        predicate=lambda v: str(v.get("flag", "")).upper() in _FLAG_WARNING,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} flagged to {v.get('flag')} — "
            f"open registry with elevated accountability risk."
        ),
    ),

    Rule(
        id="flag_state_info",
        label="Elevated-Risk Flag State Fleet",
        severity=Severity.INFO,
        predicate=lambda v: str(v.get("flag", "")).upper() in _FLAG_INFO,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} flagged to {v.get('flag')} — "
            f"flag state has documented IUU fishing incidents."
        ),
    ),

    # --- Composite risk rule ------------------------------------------------

    Rule(
        id="high_risk_score",
        label="High Composite Risk Score",
        severity=Severity.ALERT,
        predicate=lambda v: float(v.get("risk_score", 0)) >= 75,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} composite risk score"
            f" {v.get('risk_score', 0):.2f} exceeds alert threshold"
            f" ({v.get('top_reason_label', 'see details')})."
        ),
    ),
]

RULES_BY_ID: dict[str, Rule] = {r.id: r for r in RULES}
