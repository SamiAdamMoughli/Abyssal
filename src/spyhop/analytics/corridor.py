"""Corridor analysis — H3 aggregation utilities and dark-gap vector extraction.

Two concerns:

  h3_to_res5 / h3_cell_center
      Thin wrappers around the h3 library for upscaling res-7 cells to the
      ~252 km² res-5 cells used in corridor aggregation.

  compute_dark_gaps
      Derives dark-transit vectors from a vessel track sequence.  A "dark gap"
      is a pair of consecutive pings for the same MMSI where the time between
      them exceeds threshold_hours.  The from/to H3 res-5 cells, haversine
      displacement, and implied speed are returned as DarkGapVector records.

      Implied speed > 30 kn on a cargo/fishing vessel almost certainly means
      the vessel was physically present somewhere else during the dark window —
      a strong positional manipulation signal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

import h3 as _h3

DARK_GAP_THRESHOLD_HOURS: float = 6.0
HIGH_RISK_THRESHOLD: float = 70.0
MED_RISK_THRESHOLD: float = 40.0

# Implied speed above this is flagged as physically implausible for
# commercial / fishing vessels (max AIS-reported speed is typically 25 kn).
IMPLAUSIBLE_SPEED_KN: float = 30.0

# Earth radius in nautical miles (WGS-84 mean)
_R_NM = 3440.065


# ---------------------------------------------------------------------------
# H3 helpers
# ---------------------------------------------------------------------------


def h3_to_res5(h3_7: str) -> str:
    """Return the H3 res-5 parent of an H3 res-7 index (~252 km² cell)."""
    return _h3.h3_to_parent(h3_7, 5)


def h3_cell_center(h3_index: str) -> tuple[float, float]:
    """Return (lat, lon) centroid of an H3 cell at any resolution."""
    lat, lon = _h3.h3_to_geo(h3_index)
    return lat, lon


def geo_to_h3_5(lat: float, lon: float) -> str:
    """Encode (lat, lon) directly as an H3 res-5 index."""
    return _h3.geo_to_h3(lat, lon, 5)


# ---------------------------------------------------------------------------
# Haversine
# ---------------------------------------------------------------------------


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in nautical miles between two WGS-84 coordinates."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * _R_NM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Dark gap vectors
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DarkGapVector:
    """One dark-transit event — from disappearance to reappearance."""

    mmsi: str
    from_lat: float
    from_lon: float
    to_lat: float
    to_lon: float
    from_h3_5: str
    to_h3_5: str
    gap_hours: float
    displacement_nm: float
    implied_speed_kn: float
    implausible: bool          # True when implied_speed_kn > IMPLAUSIBLE_SPEED_KN
    dark_start: datetime
    dark_end: datetime


def compute_dark_gaps(
    tracks: Sequence[dict],
    threshold_hours: float = DARK_GAP_THRESHOLD_HOURS,
) -> list[DarkGapVector]:
    """Derive dark-transit gap vectors from an ordered vessel track sequence.

    Args:
        tracks: dicts with keys ``mmsi``, ``lat``, ``lon``, ``timestamp``
                (datetime, tz-aware) — and optionally ``h3_index_7``.
                Must be sorted oldest → newest, single MMSI.
        threshold_hours: minimum inter-ping gap to qualify as a dark transit.

    Returns:
        List of DarkGapVector, one per qualifying gap, in temporal order.
    """
    if len(tracks) < 2:
        return []

    gaps: list[DarkGapVector] = []
    for i in range(1, len(tracks)):
        prev, curr = tracks[i - 1], tracks[i]
        gap_h = (curr["timestamp"] - prev["timestamp"]).total_seconds() / 3600.0
        if gap_h < threshold_hours:
            continue

        plat, plon = prev["lat"], prev["lon"]
        clat, clon = curr["lat"], curr["lon"]

        # Resolve H3 res-5 — use stored index if available, else compute
        def _res5(row: dict) -> str:
            h7 = row.get("h3_index_7")
            if h7:
                return _h3.h3_to_parent(h7, 5)
            return _h3.geo_to_h3(row["lat"], row["lon"], 5)

        from_h3 = _res5(prev)
        to_h3 = _res5(curr)
        dist_nm = haversine_nm(plat, plon, clat, clon)
        implied_kn = dist_nm / gap_h if gap_h > 0 else 0.0

        gaps.append(
            DarkGapVector(
                mmsi=prev["mmsi"],
                from_lat=plat,
                from_lon=plon,
                to_lat=clat,
                to_lon=clon,
                from_h3_5=from_h3,
                to_h3_5=to_h3,
                gap_hours=round(gap_h, 2),
                displacement_nm=round(dist_nm, 2),
                implied_speed_kn=round(implied_kn, 2),
                implausible=implied_kn > IMPLAUSIBLE_SPEED_KN,
                dark_start=prev["timestamp"],
                dark_end=curr["timestamp"],
            )
        )
    return gaps
