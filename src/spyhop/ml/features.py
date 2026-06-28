"""Feature vector extraction for the ML risk-scoring pipeline.

Two entry points:
  extract_from_row(row)   — from a VesselPosition ORM row (training)
  extract_from_state(d)   — from a vessel_state dict (brain shadow scoring)

Feature vector is always RISK_FEATURE_NAMES-ordered, length 23.
Unknown / sentinel values (-1.0, -999.0) are clipped to 0 so the
tree-based models see a clean non-negative range.
"""
from __future__ import annotations

from typing import Any

# Ordered feature names — must stay in sync with training and serving.
RISK_FEATURE_NAMES: list[str] = [
    "speed_knots",
    "ais_gap_hours",
    "loitering_hours",
    "in_protected_area",
    "nearest_mpa_nm_clipped",
    "time_in_zone_hours",
    "border_skirting",
    "behavior_confidence",
    "rendezvous_duration_hours",
    "spoofing_flag",
    "gap_displacement_nm_clipped",
    "wave_height_m_clipped",
    "sst_at_thermal_front",
    "nearby_fishing_vessels",
    "beh_transit",
    "beh_trawling",
    "beh_loitering",
    "beh_anchored",
    "traj_grid",
    "traj_holding",
    "traj_spiral",
    "traj_anomaly",
    "rend_transship_risk",
]

BEHAVIOR_FEATURE_NAMES: list[str] = [
    "sog_mean",
    "sog_std",
    "cog_turn_rate",
    "tortuosity",
    "window_minutes",
    "n_pings",
]

BEHAVIOR_CLASSES = ["transit", "trawling", "loitering", "anchored"]


def _beh(status: str, label: str) -> float:
    return 1.0 if status == label else 0.0


def _traj(pattern: str, label: str) -> float:
    return 1.0 if pattern == label else 0.0


def _clip(v: float) -> float:
    """Replace unknown sentinel values (-1, -999) with 0."""
    return max(0.0, v)


def extract_from_state(state: dict[str, Any]) -> list[float]:
    """Build a risk feature vector from a vessel_state dict (brain format)."""
    beh = state.get("behavior_status", "")
    traj = state.get("trajectory_pattern", "")
    rend = state.get("rendezvous_meeting_class", "")
    return [
        float(state.get("sog", 0.0) or 0.0),
        float(state.get("ais_gap_hours", 0.0) or 0.0),
        float(state.get("loitering_hours", 0.0) or 0.0),
        1.0 if state.get("in_protected_area") else 0.0,
        _clip(float(state.get("nearest_mpa_nm", 0.0) or 0.0)),
        float(state.get("time_in_zone_hours", 0.0) or 0.0),
        1.0 if state.get("border_skirting") else 0.0,
        float(state.get("behavior_confidence", 0.0) or 0.0),
        float(state.get("rendezvous_duration_hours", 0.0) or 0.0),
        1.0 if state.get("spoofing_flag") else 0.0,
        _clip(float(state.get("gap_displacement_nm", 0.0) or 0.0)),
        _clip(float(state.get("wave_height_m", 0.0) or 0.0)),
        1.0 if state.get("sst_at_thermal_front") else 0.0,
        float(state.get("nearby_fishing_vessels", 0) or 0),
        _beh(beh, "transit"),
        _beh(beh, "trawling"),
        _beh(beh, "loitering"),
        _beh(beh, "anchored"),
        _traj(traj, "grid"),
        _traj(traj, "holding"),
        _traj(traj, "spiral"),
        _traj(traj, "anomaly"),
        1.0 if rend == "transship_risk" else 0.0,
    ]


def extract_from_row(row: Any) -> list[float]:
    """Build a risk feature vector from a VesselPosition ORM row."""
    beh = row.behavior_status or ""
    traj = row.trajectory_pattern or ""
    rend = row.rendezvous_meeting_class or ""
    return [
        float(row.speed_knots or 0.0),
        float(row.ais_gap_hours or 0.0),
        float(row.loitering_hours or 0.0),
        1.0 if row.in_protected_area else 0.0,
        _clip(float(row.nearest_mpa_nm or 0.0)),
        float(row.time_in_zone_hours or 0.0),
        1.0 if row.border_skirting else 0.0,
        float(row.behavior_confidence or 0.0),
        float(row.rendezvous_duration_hours or 0.0),
        1.0 if row.spoofing_flag else 0.0,
        _clip(float(row.gap_displacement_nm or 0.0)),
        _clip(float(row.wave_height_m or 0.0)),
        1.0 if row.sst_at_thermal_front else 0.0,
        float(row.nearby_fishing_vessels or 0),
        _beh(beh, "transit"),
        _beh(beh, "trawling"),
        _beh(beh, "loitering"),
        _beh(beh, "anchored"),
        _traj(traj, "grid"),
        _traj(traj, "holding"),
        _traj(traj, "spiral"),
        _traj(traj, "anomaly"),
        1.0 if rend == "transship_risk" else 0.0,
    ]
