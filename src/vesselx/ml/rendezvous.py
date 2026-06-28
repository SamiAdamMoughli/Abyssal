"""Vessel rendezvous / transhipment-risk detector.

Reads from the Redis H3 hot-layer written by the spatial worker.
Two vessels in the same H3-7 cell (~5 km²) with both SOG < 3 kn is the
primary rendezvous signal.  Vessel-type cross-matching escalates the meeting
to "transship_risk" when a fishing vessel is paired with a reefer / cargo
carrier — the classic at-sea transhipment pattern.

Redis key schema (set by spatial_worker):
  h3:{cell}  →  HSET  mmsi → JSON blob  (TTL 300 s)
  JSON blob fields: mmsi, name, lat, lon, sog, vessel_type, updated_at

The ``assess()`` function takes a sync ``redis.Redis`` client (already held by
brain/tasks.py) so there is no extra connection overhead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import ujson

RENDEZVOUS_SOG_THRESHOLD_KN = 3.0   # both vessels must be slower than this

# Vessel type tokens that indicate a fishing vessel
_FISHING = {"fishing", "fish_carrier", "fish carrier"}
# Vessel type tokens that indicate a transshipment-capable carrier
_CARRIER = {"reefer", "refrigerated_cargo", "refrigerated cargo", "cargo",
            "general_cargo", "general cargo"}


@dataclass(frozen=True)
class RendezvousSignal:
    detected: bool
    meeting_class: str          # "transship_risk" | "escort" | "none"
    partner_mmsi: str | None
    partner_type: str | None
    duration_hours: float       # approximation; exact value needs track history


_NONE = RendezvousSignal(
    detected=False, meeting_class="none",
    partner_mmsi=None, partner_type=None, duration_hours=0.0,
)


def assess(
    mmsi: str,
    h3_index: str | None,
    vessel_type: str | None,
    sog: float,
    redis_client: Any,   # sync redis.Redis — already open in brain/tasks.py
) -> RendezvousSignal:
    """Check the H3 cell for co-located slow vessels.

    Returns the first qualifying partner found (highest-severity class wins
    in caller logic — iterate the full cell if you need all pairs).
    """
    if not h3_index or sog >= RENDEZVOUS_SOG_THRESHOLD_KN:
        return _NONE

    try:
        cell_data: dict[str, str] = redis_client.hgetall(f"h3:{h3_index}")
    except Exception:
        return _NONE

    my_type = (vessel_type or "").lower().strip()

    for other_mmsi, blob_str in cell_data.items():
        if other_mmsi == mmsi:
            continue
        try:
            other: dict = ujson.loads(blob_str)
        except Exception:
            continue

        other_sog = float(other.get("sog") or 99.0)
        if other_sog >= RENDEZVOUS_SOG_THRESHOLD_KN:
            continue

        other_type = (other.get("vessel_type") or other.get("type") or "").lower().strip()

        is_transship = (
            (my_type in _FISHING and other_type in _CARRIER) or
            (other_type in _FISHING and my_type in _CARRIER)
        )

        return RendezvousSignal(
            detected=True,
            meeting_class="transship_risk" if is_transship else "escort",
            partner_mmsi=other_mmsi,
            partner_type=other_type or "unknown",
            duration_hours=0.5,   # conservative placeholder; exact from track history
        )

    return _NONE
