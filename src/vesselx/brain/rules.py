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
            f"Vessel {v.get('mmsi')} detected inside a Marine Protected Area "
            f"(h3={v.get('h3_index')}, lat={v.get('lat'):.4f}, lon={v.get('lon'):.4f})."
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
            f"Vessel {v.get('mmsi')} exhibiting {v.get('behavior_status')} pattern "
            f"inside MPA (confidence={v.get('behavior_confidence', 0):.0%})."
        ),
    ),

    Rule(
        id="mpa_skirting",
        label="Marine Protected Area Border Skirting",
        severity=Severity.WARNING,
        predicate=lambda v: bool(v.get("border_skirting")),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} sustained near-boundary movement outside MPA "
            f"(nearest={v.get('nearest_mpa_nm', -1):.1f} nm). Possible avoidance behaviour."
        ),
    ),

    Rule(
        id="extended_time_in_zone",
        label="Extended Dwell in Protected Zone",
        severity=Severity.ALERT,
        predicate=lambda v: float(v.get("time_in_zone_hours", 0)) > 4.0,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} has been inside a protected zone for "
            f"{v.get('time_in_zone_hours', 0):.1f} hours continuously."
        ),
    ),

    # --- Identity / AIS rules -----------------------------------------------

    Rule(
        id="ais_gap",
        label="Significant AIS Transmission Gap",
        severity=Severity.ALERT,
        predicate=lambda v: float(v.get("ais_gap_hours", 0)) > 2.0,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} AIS signal lost for "
            f"{v.get('ais_gap_hours', 0):.1f} hours (gap_type={v.get('gap_type', 'unknown')})."
        ),
    ),

    Rule(
        id="spoofing_detected",
        label="AIS Position Spoofing Detected",
        severity=Severity.CRITICAL,
        predicate=lambda v: bool(v.get("spoofing_flag")),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} probable AIS position spoofing — "
            f"implied speed {v.get('spoofing_max_speed_kn', 0):.1f} kn exceeds physical limits."
        ),
    ),

    Rule(
        id="iuu_blacklist",
        label="Vessel on IUU Blacklist",
        severity=Severity.CRITICAL,
        predicate=lambda v: bool(v.get("on_iuu_blacklist")),
        message=lambda v: (
            f"Vessel {v.get('mmsi')} matches an entry on the IUU vessel blacklist."
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
            f"Vessel {v.get('mmsi')} loitering in open ocean — possible gear deployment "
            f"or rendezvous (confidence={v.get('behavior_confidence', 0):.0%})."
        ),
    ),

    Rule(
        id="rendezvous_transship_risk",
        label="Vessel Rendezvous — Transhipment Risk",
        severity=Severity.ALERT,
        predicate=lambda v: v.get("rendezvous_meeting_class") == "transship_risk",
        message=lambda v: (
            f"Vessel {v.get('mmsi')} in close proximity to a "
            f"{v.get('rendezvous_partner_type', 'unknown')} vessel for "
            f"{v.get('rendezvous_duration_hours', 0):.1f} h — possible transhipment."
        ),
    ),

    Rule(
        id="dark_vessel_candidate",
        label="Dark Vessel (No Matching AIS) Candidate",
        severity=Severity.WARNING,
        predicate=lambda v: bool(v.get("is_dark_candidate")),
        message=lambda v: (
            f"SAR/radar contact at ({v.get('lat', 0):.4f}, {v.get('lon', 0):.4f}) "
            f"with no matching AIS transponder signal."
        ),
    ),

    # --- Composite risk rule ------------------------------------------------

    Rule(
        id="high_risk_score",
        label="High Composite Risk Score",
        severity=Severity.ALERT,
        predicate=lambda v: float(v.get("risk_score", 0)) >= 0.75,
        message=lambda v: (
            f"Vessel {v.get('mmsi')} composite risk score "
            f"{v.get('risk_score', 0):.2f} exceeds alert threshold "
            f"({v.get('top_reason_label', 'see details')})."
        ),
    ),
]

RULES_BY_ID: dict[str, Rule] = {r.id: r for r in RULES}
