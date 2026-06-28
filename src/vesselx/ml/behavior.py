"""Vessel behavior classifier.

Consumes KinematicFeatures and returns a (status, confidence) pair that maps
directly onto the ``behavior_status`` / ``behavior_confidence`` fields expected
by brain/rules.py.

Rule hierarchy (first match wins):
  anchored    SOG ≈ 0, sustained low fraction  → confidence 0.90
  loitering   low mean SOG + high COG variance  → confidence scales with turn rate
  trawling    slow SOG (1.5–5 kn) + repeated reversals → confidence scales with reversals
  transiting  higher SOG + low COG change       → confidence scales with SOG
  unknown     catch-all

Interface contract: status strings match the exact values checked in rules.py
predicates: "trawling" | "loitering" | "transiting" | "anchored" | "unknown".
"""
from __future__ import annotations

from dataclasses import dataclass

from vesselx.ml.kinematic import KinematicFeatures


@dataclass(frozen=True)
class BehaviorPrediction:
    status: str      # one of the five status tokens above
    confidence: float  # 0.0 – 1.0


_UNKNOWN = BehaviorPrediction(status="unknown", confidence=0.0)


def classify(features: KinematicFeatures | None) -> BehaviorPrediction:
    """Map kinematic features onto a behavior label + confidence score.

    Designed for real-time inference — pure computation, no I/O.
    Returns _UNKNOWN when features is None or has too few points.
    """
    if features is None or features.n_points < 3:
        return _UNKNOWN

    sog = features.mean_sog
    cog_chg = features.mean_cog_change_deg
    low_frac = features.low_sog_fraction
    reversals = features.direction_reversal_count

    # --- Anchored -----------------------------------------------------------
    if sog < 0.5 and low_frac > 0.90:
        return BehaviorPrediction(status="anchored", confidence=0.90)

    # --- Loitering ----------------------------------------------------------
    # Low speed but significant heading variability → waiting / drifting
    if sog < 2.5 and cog_chg > 15.0:
        # Confidence grows as turn-rate increases, bounded at 0.95
        conf = min(0.95, 0.50 + (cog_chg / 60.0) * 0.45)
        return BehaviorPrediction(status="loitering", confidence=round(conf, 2))

    # --- Trawling -----------------------------------------------------------
    # Canonical trawl: 1.5–5 kn with repeated direction reversals (tow turns)
    if 1.5 <= sog <= 5.0 and reversals >= 2:
        conf = min(0.90, 0.55 + (reversals / 10.0) * 0.35)
        return BehaviorPrediction(status="trawling", confidence=round(conf, 2))

    # --- Transiting ---------------------------------------------------------
    if sog > 5.0 and cog_chg < 10.0:
        conf = min(0.95, 0.60 + (min(sog, 25.0) / 25.0) * 0.35)
        return BehaviorPrediction(status="transiting", confidence=round(conf, 2))

    return _UNKNOWN
