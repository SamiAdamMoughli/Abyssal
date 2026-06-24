"""AIS Gap & Spoofing Detection — kinematic plausibility and deception signals.

Two distinct attack vectors that illegal operators use to evade surveillance:

1. AIS GAP ("Going Dark"):
   The transponder is switched off intentionally. When a vessel reappears, we
   compare its new position (P_B) to its last known position before the gap
   (P_A) to compute the minimum speed required to explain the displacement.

     v_required = dist(P_A, P_B) / Δt

   Interpretation:
     v_required << cruise_speed → vessel barely moved → TACTICAL_DARK
       (it was doing something locally while hiding from surveillance)
     v_required ≈ cruise_speed → vessel continued normally → TECHNICAL_FAILURE
     v_required > MAX_SURFACE_SPEED → position impossible → SPOOFING

2. AIS SPOOFING ("Ghost Ships"):
   The transponder remains on but transmits false coordinates.

   A) Kinematic violations: implied speed between consecutive pings exceeds the
      physical maximum for a surface vessel (~50 kn). Immediate hard evidence.

   B) Static coordinate anomaly: vessel reports SOG > 2 kn but barely moves
      across consecutive pings — the GPS values are synthetically frozen.

   C) Satellite footprint mismatch: position claims vessel is in location X but
      the receiving satellite's footprint doesn't include X at that time.
      NOT implemented here — requires live TLE ephemeris data.

Sources:
  Vespe et al. (2016) "Vessel pattern knowledge discovery" — gap thresholds
  Millefiori et al. (2021) "AIS-based maritime anomaly detection" — v_required
  Global Fishing Watch "Dark Vessels" 2021 — operational gap context
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Sequence

from spyhop.analytics.motion_profile import MotionPing, _haversine_km

NM_PER_KM = 0.539957

# Physical maximum for fast-attack craft / naval hydrofoils; normal merchant
# vessels top out at ~25 kn. Anything above 50 kn on AIS is physically impossible.
MAX_SURFACE_SPEED_KN = 50.0

# Minimum gap hours to run kinematic gap analysis (shorter gaps are noise)
MIN_GAP_H = 2.0

# A vessel is considered TACTICAL_DARK when its required speed during the gap
# is less than this fraction of its declared cruise speed (it barely moved
# despite having time to cover a large distance).
TACTICAL_DARK_SPEED_RATIO = 0.25


class GapType(str, Enum):
    TACTICAL_DARK = "tactical_dark"
    TECHNICAL_FAILURE = "technical_failure"
    SPOOFING = "spoofing"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class GapAnalysis:
    gap_hours: float
    displacement_nm: float
    required_speed_kn: float
    cruise_speed_kn: float
    gap_type: GapType


@dataclass(frozen=True)
class KinematicResult:
    has_violation: bool
    max_implied_speed_kn: float
    violation_count: int


@dataclass(frozen=True)
class StaticCoordResult:
    has_static_coords: bool
    static_fraction: float


@dataclass(frozen=True)
class SpoofingAnalysis:
    kinematic: KinematicResult
    static_coords: StaticCoordResult

    @property
    def is_suspicious(self) -> bool:
        return self.kinematic.has_violation or self.static_coords.has_static_coords


# ---------------------------------------------------------------------------
# 1 — Gap kinematic plausibility
# ---------------------------------------------------------------------------


def analyze_gap(
    last_lat: float,
    last_lon: float,
    last_ts: datetime,
    current_lat: float,
    current_lon: float,
    current_ts: datetime,
    cruise_speed_kn: float = -1.0,
) -> Optional[GapAnalysis]:
    """Classify an AIS gap by the kinematic plausibility of the reappearance.

    Parameters
    ----------
    last_lat, last_lon, last_ts:
        Last confirmed position before the gap (P_A).
    current_lat, current_lon, current_ts:
        Position at reappearance (P_B).
    cruise_speed_kn:
        Declared or historical average SOG.  -1 means unknown.

    Returns None if the gap is shorter than MIN_GAP_H.
    """
    gap_h = (current_ts - last_ts).total_seconds() / 3600.0
    if gap_h < MIN_GAP_H:
        return None

    dist_km = _haversine_km(last_lat, last_lon, current_lat, current_lon)
    dist_nm = dist_km * NM_PER_KM
    required_kn = dist_nm / gap_h if gap_h > 0 else 0.0

    if required_kn > MAX_SURFACE_SPEED_KN:
        gap_type = GapType.SPOOFING
    elif (
        cruise_speed_kn > 0
        and required_kn < TACTICAL_DARK_SPEED_RATIO * cruise_speed_kn
    ):
        gap_type = GapType.TACTICAL_DARK
    elif cruise_speed_kn > 0 and required_kn > 1.6 * cruise_speed_kn:
        gap_type = GapType.SPOOFING
    elif required_kn < 0.5:
        gap_type = GapType.TACTICAL_DARK
    else:
        gap_type = GapType.TECHNICAL_FAILURE

    return GapAnalysis(
        gap_hours=gap_h,
        displacement_nm=dist_nm,
        required_speed_kn=required_kn,
        cruise_speed_kn=max(cruise_speed_kn, 0.0),
        gap_type=gap_type,
    )


# ---------------------------------------------------------------------------
# 2A — Kinematic violation detection
# ---------------------------------------------------------------------------


def detect_kinematic_violations(
    pings: Sequence[MotionPing],
    max_speed_kn: float = MAX_SURFACE_SPEED_KN,
) -> KinematicResult:
    """Find consecutive ping pairs whose implied speed exceeds the physical max.

    A single violation is near-definitive evidence of coordinate spoofing:
    no real surface vessel can travel 50+ knots.
    """
    if len(pings) < 2:
        return KinematicResult(
            has_violation=False,
            max_implied_speed_kn=0.0,
            violation_count=0,
        )

    max_kn = 0.0
    violations = 0

    for i in range(len(pings) - 1):
        dt_h = (pings[i + 1].ts - pings[i].ts).total_seconds() / 3600.0
        if dt_h <= 0:
            continue
        dist_km = _haversine_km(
            pings[i].lat, pings[i].lon,
            pings[i + 1].lat, pings[i + 1].lon,
        )
        speed_kn = dist_km * NM_PER_KM / dt_h
        if speed_kn > max_kn:
            max_kn = speed_kn
        if speed_kn > max_speed_kn:
            violations += 1

    return KinematicResult(
        has_violation=violations > 0,
        max_implied_speed_kn=max_kn,
        violation_count=violations,
    )


# ---------------------------------------------------------------------------
# 2C — Static coordinate detection
# ---------------------------------------------------------------------------


def detect_static_coords(
    pings: Sequence[MotionPing],
    min_expected_move_km: float = 0.05,
) -> StaticCoordResult:
    """Detect pings where position doesn't change despite reported SOG > 2 kn.

    Legitimate vessels always have some natural drift.  Software-generated
    fake positions often hold perfectly still while reporting forward motion.
    """
    if len(pings) < 3:
        return StaticCoordResult(has_static_coords=False, static_fraction=0.0)

    moving = [p for p in pings if p.sog > 2.0]
    if len(moving) < 3:
        return StaticCoordResult(has_static_coords=False, static_fraction=0.0)

    frozen_count = 0
    checks = 0

    for i in range(1, len(moving)):
        dt_h = (moving[i].ts - moving[i - 1].ts).total_seconds() / 3600.0
        if dt_h <= 0:
            continue
        dist_km = _haversine_km(
            moving[i - 1].lat, moving[i - 1].lon,
            moving[i].lat, moving[i].lon,
        )
        expected_min_km = moving[i - 1].sog * 1.852 * dt_h * 0.05
        if dist_km < max(expected_min_km, min_expected_move_km):
            frozen_count += 1
        checks += 1

    if checks == 0:
        return StaticCoordResult(has_static_coords=False, static_fraction=0.0)

    frac = frozen_count / checks
    return StaticCoordResult(
        has_static_coords=frac > 0.50,
        static_fraction=frac,
    )


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


def analyze_spoofing(pings: Sequence[MotionPing]) -> Optional[SpoofingAnalysis]:
    """Run both kinematic and static-coord checks on a ping sequence.

    Returns None when fewer than 2 pings are available.
    """
    if len(pings) < 2:
        return None
    return SpoofingAnalysis(
        kinematic=detect_kinematic_violations(pings),
        static_coords=detect_static_coords(pings),
    )
