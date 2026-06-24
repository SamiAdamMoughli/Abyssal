"""Spatial risk features — zone proximity, border skirting, time in zone.

Complements the binary is_in_protected_area check with three quantitative
signals derived from the vessel's relationship to zone boundaries:

  nearest_mpa_nm      — metres to the nearest MPA boundary
                        0.0 = inside; -1.0 = no zone geometry loaded
  time_in_zone_hours  — consecutive hours vessel has been inside its current zone
                        (measured from the oldest contiguous inside-ping in
                         the sliding window through to now)
  border_skirting     — True when ≥70% of the recent window is spent within
                        PROXIMITY_NM of a boundary while never crossing into it

All functions accept plain Python values (lat/lon, MotionPing sequences) so
they can be tested without a DB connection. DB querying happens in tasks.py.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from spyhop.analytics.motion_profile import MotionPing

# Vessels within this buffer trigger a pre-alert even without MPA entry.
PROXIMITY_NM = 5.0

# Minimum fraction of track pings that must be in the proximity band
# for the border-skirting signal to fire.
SKIRTING_MIN_FRACTION = 0.70

# Minimum window (hours) of track history required for skirting detection.
SKIRTING_MIN_WINDOW_H = 2.0


# ---------------------------------------------------------------------------
# Data contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpatialFeatures:
    nearest_mpa_nm: float       # 0.0 if inside; -1.0 if unknown
    time_in_zone_hours: float   # hours of current continuous zone presence
    border_skirting: bool       # sustained near-boundary without entering
    skirting_fraction: float    # fraction of window pings in proximity band


# ---------------------------------------------------------------------------
# Core computations (pure, no DB deps)
# ---------------------------------------------------------------------------


def nearest_zone_nm(lat: float, lon: float) -> float:
    """Distance to nearest MPA boundary in nautical miles.

    Delegates to geo.py's cached Shapely geometry. Returns -1.0 when no
    zone data is available (graceful: missing geometry → no signal).
    """
    try:
        from backend.app.geo import distance_to_nearest_zone_nm
        return distance_to_nearest_zone_nm(lat, lon)
    except Exception:
        return -1.0


def time_in_zone(pings: Sequence[MotionPing]) -> float:
    """Hours the vessel has been continuously inside a zone.

    Walks the ping window from the most recent ping backward and counts
    consecutive inside-zone pings. Returns 0.0 if the vessel is currently
    outside the zone, or if fewer than 2 pings are available.

    Args:
        pings: ordered oldest→newest MotionPing sequence
    """
    if len(pings) < 2:
        return 0.0

    try:
        from backend.app.geo import get_zone_geometry
        from shapely.geometry import Point
        zone = get_zone_geometry()
    except Exception:
        return 0.0

    if zone is None:
        return 0.0

    # Walk newest→oldest; find the earliest ping in the current inside streak.
    streak_start = None
    for ping in reversed(pings):
        if zone.covers(Point(ping.lon, ping.lat)):
            streak_start = ping.ts
        else:
            break  # streak broken — stop here

    if streak_start is None:
        return 0.0

    last_ts = pings[-1].ts
    return (last_ts - streak_start).total_seconds() / 3600.0


def border_skirting(
    pings: Sequence[MotionPing],
    threshold_nm: float = PROXIMITY_NM,
    min_window_hours: float = SKIRTING_MIN_WINDOW_H,
    min_fraction: float = SKIRTING_MIN_FRACTION,
) -> tuple[bool, float]:
    """Detect sustained near-boundary behaviour without zone entry.

    A vessel is skirting when:
      1. It has been outside the zone for the entire window (never entered).
      2. ≥min_fraction of pings are within threshold_nm of the boundary.
      3. The window spans at least min_window_hours.

    Returns:
        (is_skirting, fraction_of_pings_in_proximity_band)
    """
    if len(pings) < 3:
        return False, 0.0

    window_h = (pings[-1].ts - pings[0].ts).total_seconds() / 3600.0
    if window_h < min_window_hours:
        return False, 0.0

    try:
        from backend.app.geo import get_zone_geometry, is_in_protected_area
    except Exception:
        return False, 0.0

    # If vessel entered zone at any point → not skirting (it went inside)
    for ping in pings:
        if is_in_protected_area(ping.lat, ping.lon):
            return False, 0.0

    # Count pings within threshold_nm of boundary
    near = sum(
        1
        for ping in pings
        if 0.0 <= nearest_zone_nm(ping.lat, ping.lon) <= threshold_nm
    )
    fraction = near / len(pings)
    return fraction >= min_fraction, fraction


# ---------------------------------------------------------------------------
# Batch convenience (accepts track rows already fetched from DB)
# ---------------------------------------------------------------------------


def compute_spatial_features(pings: Sequence[MotionPing]) -> SpatialFeatures:
    """Derive all spatial features from a ping sequence in one pass.

    Args:
        pings: ordered oldest→newest MotionPing sequence for one vessel
    """
    if not pings:
        return SpatialFeatures(
            nearest_mpa_nm=-1.0,
            time_in_zone_hours=0.0,
            border_skirting=False,
            skirting_fraction=0.0,
        )

    last = pings[-1]
    nm = nearest_zone_nm(last.lat, last.lon)
    t_zone = time_in_zone(pings)
    is_skirting, skirt_frac = border_skirting(pings)

    return SpatialFeatures(
        nearest_mpa_nm=nm,
        time_in_zone_hours=t_zone,
        border_skirting=is_skirting,
        skirting_fraction=skirt_frac,
    )
