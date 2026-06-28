"""AIS position spoofing detector.

Uses the max_implied_speed_kn field already computed by the kinematic extractor.
A vessel is flagged when any consecutive position pair implies a speed that
exceeds the physical limit for surface vessels (~45 kn covers even the fastest
military craft; commercial fishing / cargo vessels rarely exceed 25 kn).

Separate from kinematic.py so it can be called independently when only
speed-jump detection is needed (e.g., the on-demand MMSI evaluation path).
"""
from __future__ import annotations

from dataclasses import dataclass

from vesselx.ml.kinematic import KinematicFeatures

MAX_PHYSICAL_SPEED_KN = 45.0  # upper bound for any surface vessel


@dataclass(frozen=True)
class SpoofingAssessment:
    flag: bool
    max_implied_speed_kn: float
    reason: str


_CLEAN = SpoofingAssessment(flag=False, max_implied_speed_kn=0.0, reason="clean")
_NO_DATA = SpoofingAssessment(flag=False, max_implied_speed_kn=0.0, reason="insufficient_data")


def assess(features: KinematicFeatures | None) -> SpoofingAssessment:
    """Evaluate spoofing likelihood from pre-computed kinematic features.

    Returns a SpoofingAssessment; the ``flag`` field maps directly onto the
    ``spoofing_flag`` key expected by brain/rules.py and the
    ``max_implied_speed_kn`` maps onto ``spoofing_max_speed_kn``.
    """
    if features is None or features.n_points < 2:
        return _NO_DATA

    spd = features.max_implied_speed_kn
    if spd > MAX_PHYSICAL_SPEED_KN:
        return SpoofingAssessment(
            flag=True,
            max_implied_speed_kn=spd,
            reason=f"implied_{spd:.0f}kn_exceeds_{MAX_PHYSICAL_SPEED_KN:.0f}kn_limit",
        )

    return SpoofingAssessment(flag=False, max_implied_speed_kn=spd, reason="clean")
