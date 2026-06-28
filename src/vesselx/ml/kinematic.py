"""Kinematic feature extraction from a vessel track-point ring buffer.

Input: a sequence of raw track-point dicts (lat, lon, sog, cog, ts).
Output: KinematicFeatures dataclass consumed by the behavior classifier
        and the spoofing detector.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Sequence

_EARTH_R_NM = 3440.065  # Earth radius in nautical miles


@dataclass(frozen=True)
class KinematicFeatures:
    """Pre-computed kinematic statistics over a vessel track window."""

    n_points: int
    mean_sog: float           # knots
    std_sog: float            # knots
    mean_cog_change_deg: float  # mean absolute COG delta between points
    low_sog_fraction: float   # fraction of points with SOG < 2 kn
    direction_reversal_count: int   # consecutive COG changes > 90 °
    max_implied_speed_kn: float     # highest speed implied by position+time


def _cog_delta(a: float, b: float) -> float:
    """Shortest-arc absolute difference between two COG headings (°)."""
    return abs((b - a + 180.0) % 360.0 - 180.0)


def _haversine_nm(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return 2.0 * _EARTH_R_NM * math.asin(math.sqrt(a))


def _parse_ts(ts: str | datetime | None) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return ts
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def extract(track_points: Sequence[dict]) -> KinematicFeatures | None:
    """Compute kinematic features from a sequence of track-point dicts.

    Each point is expected to have: lat, lon, sog, cog, ts (ISO-8601).
    Returns None when fewer than 2 valid points are available.
    """
    pts = [
        p for p in track_points
        if p.get("sog") is not None and p.get("lat") is not None
    ]
    if len(pts) < 2:
        return None

    sogs = [float(p["sog"]) for p in pts]
    cogs = [float(p.get("cog") or 0.0) for p in pts]

    mean_sog = sum(sogs) / len(sogs)
    variance = sum((s - mean_sog) ** 2 for s in sogs) / len(sogs)
    std_sog = math.sqrt(variance)
    low_sog_fraction = sum(1 for s in sogs if s < 2.0) / len(sogs)

    cog_deltas = [
        _cog_delta(cogs[i], cogs[i + 1]) for i in range(len(cogs) - 1)
    ]
    mean_cog_change = (
        sum(cog_deltas) / len(cog_deltas) if cog_deltas else 0.0
    )
    direction_reversal_count = sum(1 for d in cog_deltas if d > 90.0)

    # Implied speed from consecutive position + timestamp pairs
    max_implied = 0.0
    for i in range(len(pts) - 1):
        p1, p2 = pts[i], pts[i + 1]
        t1, t2 = _parse_ts(p1.get("ts")), _parse_ts(p2.get("ts"))
        if t1 is None or t2 is None:
            continue
        dt_h = abs((t2 - t1).total_seconds()) / 3600.0
        if dt_h < 1e-4:
            continue
        try:
            dist = _haversine_nm(
                float(p1["lat"]), float(p1["lon"]),
                float(p2["lat"]), float(p2["lon"]),
            )
            max_implied = max(max_implied, dist / dt_h)
        except (KeyError, TypeError, ValueError):
            continue

    return KinematicFeatures(
        n_points=len(pts),
        mean_sog=round(mean_sog, 2),
        std_sog=round(std_sog, 2),
        mean_cog_change_deg=round(mean_cog_change, 2),
        low_sog_fraction=round(low_sog_fraction, 3),
        direction_reversal_count=direction_reversal_count,
        max_implied_speed_kn=round(max_implied, 1),
    )
