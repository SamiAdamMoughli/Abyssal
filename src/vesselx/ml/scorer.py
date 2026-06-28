"""Composite vessel risk scorer.

Produces a 0–1 ``risk_score`` from all available anomaly signals and
identifies the single highest-weight contributor as ``top_reason_label``.

Scoring model: additive contributions passed through a saturation function
    score = 1 − exp(−λ · Σ wᵢ)   where λ = 1.2

This gives diminishing returns so that a vessel flagged for 5 minor signals
does not trivially beat one flagged for a single CRITICAL signal.

The ``compute()`` function is pure — no I/O — so it can be called in any
context including the Celery task hot path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

_LAMBDA = 1.2  # saturation rate — tune upward to increase score sensitivity

# (signal_key_or_logic, label, weight)
# Weight represents maximum contribution from this single signal.
_SIGNAL_WEIGHTS: list[tuple[str, str, float]] = [
    ("on_iuu_blacklist",        "IUU blacklist",        1.00),
    ("spoofing_flag",           "AIS spoofing",         0.85),
    ("in_protected_area",       "MPA incursion",        0.80),
    ("rendezvous_transship",    "Rendezvous/transship", 0.70),
    ("ais_gap",                 "AIS gap",              0.60),   # scaled by gap duration
    ("loitering",               "Loitering",            0.45),   # scaled by confidence
    ("trawling",                "Trawling pattern",     0.40),   # scaled by confidence
    ("border_skirting",         "MPA border skirting",  0.30),
]


@dataclass(frozen=True)
class RiskScore:
    score: float            # 0.0 – 1.0, three decimal places
    top_reason_label: str   # human-readable label of the dominant signal


_ZERO = RiskScore(score=0.0, top_reason_label="")


def compute(vessel_state: dict) -> RiskScore:
    """Derive composite risk score from a vessel_state dict.

    Reads the same keys used by brain/rules.py predicates so the scorer and
    the rule engine always agree on signal values.
    """
    contributions: dict[str, float] = {}

    if vessel_state.get("on_iuu_blacklist"):
        contributions["IUU blacklist"] = 1.00

    if vessel_state.get("spoofing_flag"):
        contributions["AIS spoofing"] = 0.85

    if vessel_state.get("in_protected_area"):
        contributions["MPA incursion"] = 0.80

    if vessel_state.get("rendezvous_meeting_class") == "transship_risk":
        contributions["Rendezvous/transship"] = 0.70

    gap = float(vessel_state.get("ais_gap_hours") or 0.0)
    if gap > 1.0:
        # Scales from ~0.025 at 1 h to the full 0.60 cap at 24 h
        contributions["AIS gap"] = min(0.60, (gap / 24.0) * 0.60)

    bhv = vessel_state.get("behavior_status", "")
    conf = float(vessel_state.get("behavior_confidence") or 0.0)
    if bhv == "loitering" and conf >= 0.60:
        contributions["Loitering"] = conf * 0.45
    elif bhv == "trawling" and conf >= 0.60:
        contributions["Trawling pattern"] = conf * 0.40

    if vessel_state.get("border_skirting"):
        contributions["MPA border skirting"] = 0.30

    if not contributions:
        return _ZERO

    top_label = max(contributions, key=lambda k: contributions[k])
    raw_sum = sum(contributions.values())
    score = round(1.0 - math.exp(-_LAMBDA * raw_sum), 3)

    return RiskScore(score=min(1.0, score), top_reason_label=top_label)
