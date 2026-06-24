"""Motion Profile Analysis — vessel behaviour classification from AIS ping history.

Derives three kinematic features from a sliding window of GPS pings:

  SOG variance     σ_SOG   — speed profile standard deviation (knots)
  COG turn rate    Δθ/Δt   — mean |ΔCOG/Δt| in degrees/minute, 360°-safe
  Tortuosity       τ       — actual path length / straight-line distance

These three features produce a unique mathematical signature for each
activity type:

  Transit   — SOG high & constant, τ ≈ 1.0, near-zero turn rate
  Trawling  — SOG 2–5 kn, tortuous path, moderate rhythmic turning
  Loitering — SOG minimal, chaotic high-rate turning, τ >> 1
  Anchored  — SOG ≈ 0 (GPS noise only)
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Sequence


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MotionPing:
    """One AIS position report in the sliding window."""
    lat: float
    lon: float
    sog: float      # speed over ground, knots
    cog: float      # course over ground, degrees 0–360
    ts: datetime    # UTC timestamp


@dataclass(frozen=True)
class MotionFeatures:
    """Extracted kinematic features for one vessel window."""
    sog_mean: float
    sog_std: float
    cog_turn_rate: float    # mean |ΔCOG/Δt| in degrees/minute
    tortuosity: float       # τ = D_actual / D_direct
    window_minutes: float   # time span covered by the window
    n_pings: int


class BehaviorClass(str, Enum):
    TRANSIT   = "transit"
    TRAWLING  = "trawling"
    LOITERING = "loitering"
    ANCHORED  = "anchored"
    UNKNOWN   = "unknown"


@dataclass(frozen=True)
class MotionProfile:
    behavior:   BehaviorClass
    confidence: float           # 0.0 – 1.0
    features:   MotionFeatures


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

_R_KM = 6371.0


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km (Haversine formula)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    return 2.0 * _R_KM * math.asin(math.sqrt(min(a, 1.0)))


def _angular_diff(a: float, b: float) -> float:
    """Smallest unsigned angle between two bearings, result in [0, 180]."""
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


def compute_features(pings: Sequence[MotionPing]) -> Optional[MotionFeatures]:
    """Compute kinematic features from an ordered sequence of AIS pings.

    Returns None when fewer than 3 pings are present — the minimum needed
    for a meaningful tortuosity and turn-rate estimate.
    """
    if len(pings) < 3:
        return None

    sogs = [p.sog for p in pings]
    sog_mean = statistics.mean(sogs)
    sog_std  = statistics.stdev(sogs) if len(sogs) > 1 else 0.0

    # COG turn rate — degrees per minute, 360°-safe
    turn_rates: list[float] = []
    for i in range(1, len(pings)):
        dt_min = (pings[i].ts - pings[i - 1].ts).total_seconds() / 60.0
        if dt_min > 0:
            dcog = _angular_diff(pings[i].cog, pings[i - 1].cog)
            turn_rates.append(dcog / dt_min)
    cog_turn_rate = statistics.mean(turn_rates) if turn_rates else 0.0

    # Tortuosity τ = Σ(consecutive distances) / direct distance
    d_actual = sum(
        _haversine_km(pings[i].lat, pings[i].lon, pings[i + 1].lat, pings[i + 1].lon)
        for i in range(len(pings) - 1)
    )
    d_direct = _haversine_km(
        pings[0].lat, pings[0].lon, pings[-1].lat, pings[-1].lon
    )
    # Guard: vessel barely moved → high τ signals loitering/anchored
    tortuosity = d_actual / d_direct if d_direct >= 0.05 else float(len(pings))

    window_minutes = (pings[-1].ts - pings[0].ts).total_seconds() / 60.0

    return MotionFeatures(
        sog_mean=sog_mean,
        sog_std=sog_std,
        cog_turn_rate=cog_turn_rate,
        tortuosity=tortuosity,
        window_minutes=window_minutes,
        n_pings=len(pings),
    )


# ---------------------------------------------------------------------------
# Behaviour classifier
# ---------------------------------------------------------------------------
# Thresholds derived from:
#   Marzuki et al. (2018) "Marine vessel classification using AIS"
#   GFW fishing detection algorithm (public documentation)
#   Halpern et al. (2012) global fishing footprint analysis


def classify(f: MotionFeatures) -> MotionProfile:
    """Map kinematic features to a behaviour class with a confidence score."""

    # --- Anchored / drifting: essentially stationary ----------------------
    if f.sog_mean < 0.5:
        return MotionProfile(
            behavior=BehaviorClass.ANCHORED,
            confidence=1.0,
            features=f,
        )

    # --- Transit: fast, straight, speed-stable ----------------------------
    transit_hits = sum([
        f.sog_mean > 7.0,
        f.sog_std  < 1.5,
        f.tortuosity < 1.3,
        f.cog_turn_rate < 0.5,
    ])
    if transit_hits >= 3 and f.sog_mean > 7.0:
        return MotionProfile(
            behavior=BehaviorClass.TRANSIT,
            confidence=transit_hits / 4,
            features=f,
        )

    # --- Trawling: slow, tortuous, rhythmic moderate turns ----------------
    trawling_hits = sum([
        2.0 <= f.sog_mean <= 5.5,
        f.sog_std  < 2.5,
        f.tortuosity > 1.3,
        0.3 <= f.cog_turn_rate <= 5.0,
    ])
    if trawling_hits >= 3:
        return MotionProfile(
            behavior=BehaviorClass.TRAWLING,
            confidence=trawling_hits / 4,
            features=f,
        )

    # --- Loitering: very slow, chaotic turning, extreme tortuosity --------
    loitering_hits = sum([
        0.4 < f.sog_mean < 3.5,
        f.tortuosity > 2.5,
        f.cog_turn_rate > 2.5,
    ])
    if loitering_hits >= 2:
        return MotionProfile(
            behavior=BehaviorClass.LOITERING,
            confidence=loitering_hits / 3,
            features=f,
        )

    return MotionProfile(
        behavior=BehaviorClass.UNKNOWN,
        confidence=0.0,
        features=f,
    )


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


def profile_from_pings(pings: Sequence[MotionPing]) -> Optional[MotionProfile]:
    """Compute features then classify.  Returns None if fewer than 3 pings."""
    features = compute_features(pings)
    if features is None:
        return None
    return classify(features)
