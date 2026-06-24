"""Trajectory Pattern Recognition — geometric fingerprints of maritime routes.

Analyzes the full geometric shape of a vessel's 6-24 hour track rather
than instantaneous speed or position. Where the motion profile asks *how*
a vessel moves, trajectory analysis asks *what shape* it draws on the chart.

Pipeline:
  1. resample_track()   — normalise irregular AIS pings to fixed intervals
  2. douglas_peucker()  — compress out collinear noise, keep inflections
  3. extract_features() — turn alternation, path closure, leg regularity
  4. classify_trajectory() — map features to one of five geometry classes

Geometric patterns and their maritime meaning:
  GRID    — parallel runs with ~90° turns: trawling / benthic survey
  HOLDING — closed loop or figure-8: waiting, rendezvous, STS transfer
  SPIRAL  — expanding/contracting arcs: SAR, manta towing
  TRANSIT — straight or gently arcing point-to-point passage
  ANOMALY — a non-fishing vessel whose track matches a fishing pattern
  UNKNOWN — insufficient data or no dominant pattern

No third-party ML dependencies — pure Python / standard library.
DTW distance is provided for future DBSCAN clustering (not yet scored).
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Optional, Sequence

from spyhop.analytics.motion_profile import MotionPing, _haversine_km

MIN_WINDOW_HOURS = 2.0
DP_EPSILON_DEG = 0.004
MIN_SIGNIFICANT_TURN_DEG = 18.0


# ---------------------------------------------------------------------------
# Data contracts
# ---------------------------------------------------------------------------


class TrajectoryPattern(str, Enum):
    GRID = "grid"
    HOLDING = "holding"
    SPIRAL = "spiral"
    TRANSIT = "transit"
    ANOMALY = "anomaly"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TrajectoryFeatures:
    n_significant_turns: int
    mean_turn_angle: float
    turn_alternation_score: float
    path_closure_ratio: float
    leg_regularity: float
    window_hours: float
    n_compressed: int


@dataclass(frozen=True)
class TrajectoryProfile:
    pattern: TrajectoryPattern
    confidence: float
    features: TrajectoryFeatures


# ---------------------------------------------------------------------------
# Step 1 — Resampling
# ---------------------------------------------------------------------------


def resample_track(
    pings: Sequence[MotionPing],
    interval_minutes: float = 5.0,
) -> list[MotionPing]:
    """Interpolate an irregular AIS ping sequence to fixed time intervals.

    Linear lat/lon interpolation. Ensures Douglas-Peucker compression is
    not biased toward areas with denser AIS reporting.
    """
    if len(pings) < 2:
        return list(pings)

    result: list[MotionPing] = []
    interval = timedelta(minutes=interval_minutes)
    t = pings[0].ts
    t_end = pings[-1].ts
    i = 0

    while t <= t_end:
        while i < len(pings) - 2 and pings[i + 1].ts <= t:
            i += 1
        p0, p1 = pings[i], pings[min(i + 1, len(pings) - 1)]
        span = (p1.ts - p0.ts).total_seconds()
        alpha = (t - p0.ts).total_seconds() / span if span > 0 else 0.0
        alpha = max(0.0, min(1.0, alpha))
        result.append(MotionPing(
            lat=p0.lat + alpha * (p1.lat - p0.lat),
            lon=p0.lon + alpha * (p1.lon - p0.lon),
            sog=p0.sog + alpha * (p1.sog - p0.sog),
            cog=p0.cog,
            ts=t,
        ))
        t += interval

    return result


# ---------------------------------------------------------------------------
# Step 2 — Douglas-Peucker simplification
# ---------------------------------------------------------------------------


def _perpendicular_dist(
    lat: float, lon: float,
    lat_a: float, lon_a: float,
    lat_b: float, lon_b: float,
) -> float:
    """Perpendicular distance from P to segment AB, in degrees.

    Planar approximation — adequate for segments shorter than ~100 nm.
    """
    dlat = lat_b - lat_a
    dlon = lon_b - lon_a
    seg_len_sq = dlat * dlat + dlon * dlon
    if seg_len_sq == 0:
        return math.sqrt((lat - lat_a) ** 2 + (lon - lon_a) ** 2)
    t = ((lat - lat_a) * dlat + (lon - lon_a) * dlon) / seg_len_sq
    t = max(0.0, min(1.0, t))
    proj_lat = lat_a + t * dlat
    proj_lon = lon_a + t * dlon
    return math.sqrt((lat - proj_lat) ** 2 + (lon - proj_lon) ** 2)


def douglas_peucker(
    pings: Sequence[MotionPing],
    epsilon: float = DP_EPSILON_DEG,
) -> list[MotionPing]:
    """Ramer-Douglas-Peucker path simplification.

    Removes collinear noise while preserving inflection points.
    Reduces typical AIS ping counts by ~70-80 % with shape intact.
    """
    if len(pings) <= 2:
        return list(pings)

    max_dist = 0.0
    max_idx = 0
    a, b = pings[0], pings[-1]

    for i in range(1, len(pings) - 1):
        d = _perpendicular_dist(
            pings[i].lat, pings[i].lon,
            a.lat, a.lon, b.lat, b.lon,
        )
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > epsilon:
        left = douglas_peucker(pings[:max_idx + 1], epsilon)
        right = douglas_peucker(pings[max_idx:], epsilon)
        return left[:-1] + right
    return [pings[0], pings[-1]]


# ---------------------------------------------------------------------------
# Step 3 — Feature extraction
# ---------------------------------------------------------------------------


def extract_features(
    pings: Sequence[MotionPing],
) -> Optional[TrajectoryFeatures]:
    """Extract geometric fingerprint from a DP-compressed ping sequence.

    Returns None if fewer than 3 pings span MIN_WINDOW_HOURS.
    """
    if len(pings) < 3:
        return None

    window_hours = (
        (pings[-1].ts - pings[0].ts).total_seconds() / 3600.0
    )
    if window_hours < MIN_WINDOW_HOURS:
        return None

    def _bearing(p0: MotionPing, p1: MotionPing) -> float:
        dlat = p1.lat - p0.lat
        dlon = (p1.lon - p0.lon) * math.cos(
            math.radians((p0.lat + p1.lat) / 2)
        )
        return math.degrees(math.atan2(dlon, dlat)) % 360

    bearings = [
        _bearing(pings[i], pings[i + 1]) for i in range(len(pings) - 1)
    ]

    signed_turns: list[float] = []
    for i in range(len(bearings) - 1):
        diff = (bearings[i + 1] - bearings[i] + 180) % 360 - 180
        signed_turns.append(diff)

    sig_turns = [t for t in signed_turns if abs(t) >= MIN_SIGNIFICANT_TURN_DEG]
    n_sig = len(sig_turns)

    mean_turn = (
        statistics.mean(abs(t) for t in sig_turns) if sig_turns else 0.0
    )

    if n_sig >= 2:
        alternations = sum(
            1 for i in range(1, n_sig)
            if (sig_turns[i] > 0) != (sig_turns[i - 1] > 0)
        )
        alt_score = alternations / (n_sig - 1)
    else:
        alt_score = 0.0

    total_len = sum(
        _haversine_km(
            pings[i].lat, pings[i].lon,
            pings[i + 1].lat, pings[i + 1].lon,
        )
        for i in range(len(pings) - 1)
    )
    d_close = _haversine_km(
        pings[0].lat, pings[0].lon,
        pings[-1].lat, pings[-1].lon,
    )
    closure = d_close / total_len if total_len > 0.1 else 1.0

    if n_sig >= 3:
        inflection_dists: list[float] = []
        j = 0
        for i in range(len(signed_turns)):
            if abs(signed_turns[i]) >= MIN_SIGNIFICANT_TURN_DEG:
                seg_d = _haversine_km(
                    pings[j].lat, pings[j].lon,
                    pings[i + 1].lat, pings[i + 1].lon,
                )
                inflection_dists.append(seg_d)
                j = i + 1
        if inflection_dists and statistics.mean(inflection_dists) > 0:
            cv = (
                statistics.stdev(inflection_dists)
                / statistics.mean(inflection_dists)
            )
            regularity = max(0.0, 1.0 - cv)
        else:
            regularity = 0.0
    else:
        regularity = 0.0

    return TrajectoryFeatures(
        n_significant_turns=n_sig,
        mean_turn_angle=mean_turn,
        turn_alternation_score=alt_score,
        path_closure_ratio=closure,
        leg_regularity=regularity,
        window_hours=window_hours,
        n_compressed=len(pings),
    )


# ---------------------------------------------------------------------------
# Step 4 — Pattern classifier
# ---------------------------------------------------------------------------


def classify_trajectory(
    f: TrajectoryFeatures,
    vessel_type: str = "unknown",
) -> TrajectoryProfile:
    """Map geometric features to a trajectory pattern + confidence score."""

    FISHING_TYPES = {"trawler", "longliner", "purse_seiner", "fishing"}

    if f.n_significant_turns <= 2 or f.mean_turn_angle < 20.0:
        is_anomaly = (
            vessel_type in FISHING_TYPES
            and f.path_closure_ratio > 0.6
            and f.n_significant_turns == 0
        )
        return TrajectoryProfile(
            pattern=(
                TrajectoryPattern.ANOMALY
                if is_anomaly else TrajectoryPattern.TRANSIT
            ),
            confidence=0.8 if f.n_significant_turns == 0 else 0.6,
            features=f,
        )

    grid_hits = sum([
        f.turn_alternation_score > 0.60,
        50.0 <= f.mean_turn_angle <= 160.0,
        f.path_closure_ratio > 0.10,
        f.leg_regularity > 0.40,
    ])
    if grid_hits >= 3:
        is_anomaly = (
            vessel_type not in FISHING_TYPES
            and vessel_type not in ("unknown", "support", "")
        )
        return TrajectoryProfile(
            pattern=(
                TrajectoryPattern.ANOMALY
                if is_anomaly else TrajectoryPattern.GRID
            ),
            confidence=grid_hits / 4,
            features=f,
        )

    holding_hits = sum([
        f.path_closure_ratio < 0.30,
        f.n_significant_turns >= 4,
    ])
    if holding_hits == 2:
        return TrajectoryProfile(
            pattern=TrajectoryPattern.HOLDING,
            confidence=min(
                1.0,
                0.5
                + (0.30 - f.path_closure_ratio) * 2
                + f.n_significant_turns * 0.02,
            ),
            features=f,
        )

    spiral_hits = sum([
        f.path_closure_ratio < 0.40,
        f.turn_alternation_score < 0.35,
        f.n_significant_turns >= 4,
    ])
    if spiral_hits >= 2:
        return TrajectoryProfile(
            pattern=TrajectoryPattern.SPIRAL,
            confidence=spiral_hits / 3,
            features=f,
        )

    return TrajectoryProfile(
        pattern=TrajectoryPattern.UNKNOWN,
        confidence=0.0,
        features=f,
    )


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------


def trajectory_profile(
    pings: Sequence[MotionPing],
    vessel_type: str = "unknown",
    resample_interval_min: float = 5.0,
) -> Optional[TrajectoryProfile]:
    """Full pipeline: resample → compress → features → classify.

    Returns None when fewer than 3 pings span at least MIN_WINDOW_HOURS.
    """
    if len(pings) < 3:
        return None

    resampled = resample_track(pings, resample_interval_min)
    compressed = douglas_peucker(resampled)

    window_hours = (
        (pings[-1].ts - pings[0].ts).total_seconds() / 3600.0
    )

    if len(compressed) <= 2 and window_hours >= MIN_WINDOW_HOURS:
        transit_features = TrajectoryFeatures(
            n_significant_turns=0,
            mean_turn_angle=0.0,
            turn_alternation_score=0.0,
            path_closure_ratio=1.0,
            leg_regularity=0.0,
            window_hours=window_hours,
            n_compressed=len(compressed),
        )
        return TrajectoryProfile(
            pattern=TrajectoryPattern.TRANSIT,
            confidence=0.9,
            features=transit_features,
        )

    features = extract_features(compressed)
    if features is None:
        return None

    return classify_trajectory(features, vessel_type)


# ---------------------------------------------------------------------------
# DTW distance (foundation for future DBSCAN clustering)
# ---------------------------------------------------------------------------


def dtw_distance(
    track_a: Sequence[MotionPing],
    track_b: Sequence[MotionPing],
) -> float:
    """Dynamic Time Warping distance between two ping sequences.

    Uses haversine distance in km. O(N * M) — suitable for tracks up to
    ~200 pings each. Returns float('inf') if either track is empty.
    """
    n, m = len(track_a), len(track_b)
    if n == 0 or m == 0:
        return float("inf")

    INF = float("inf")
    prev = [INF] * (m + 1)
    prev[0] = 0.0

    for i in range(1, n + 1):
        curr = [INF] * (m + 1)
        for j in range(1, m + 1):
            cost = _haversine_km(
                track_a[i - 1].lat, track_a[i - 1].lon,
                track_b[j - 1].lat, track_b[j - 1].lon,
            )
            curr[j] = cost + min(prev[j], curr[j - 1], prev[j - 1])
        prev = curr

    return prev[m]
