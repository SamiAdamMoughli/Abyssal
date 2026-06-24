"""Vessel-to-Vessel Interaction Analysis — Pair Classification.

Classifies the TYPE of encounter between two proximate, slow-moving vessels.
This module contains only pure data types and classification logic; the
proximity detection and Redis state machine live in the Celery worker.

Design principle:
  The pair type (fishing + reefer = transshipment) carries far more
  information than duration alone. A 30-minute fishing+reefer encounter
  at 0.2 nm in the middle of the Pacific is stronger evidence than a
  6-hour cargo+cargo encounter in a shipping lane.

Meeting types and their maritime meaning:
  TRANSSHIPMENT      — fishing vessel + reefer/carrier: fish handoff
  BUNKERING          — any vessel + tanker: at-sea refuelling
  FISHING_COORD      — fisher + fisher: gear exchange, crew transfer
  PORT_ASSIST        — tug + cargo: routine harbour manoeuvre (low risk)
  VESSEL_TO_VESSEL   — unclassified slow-moving pair
  UNKNOWN            — too little type data to classify
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MeetingClass(str, Enum):
    TRANSSHIPMENT = "transshipment"
    BUNKERING = "bunkering"
    FISHING_COORD = "fishing_coord"
    PORT_ASSIST = "port_assist"
    VESSEL_TO_VESSEL = "vessel_to_vessel"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InteractionResult:
    partner_mmsi: str
    partner_type: str
    meeting_class: MeetingClass
    duration_h: float
    dist_nm: float


_FISHING = {"fishing", "trawler", "longliner", "purse_seiner",
            "squid_jigger", "drifting_longlines", "set_gillnets"}
_REEFER = {"reefer", "carrier", "refrigerated", "fish_carrier"}
_TANKER = {"tanker", "bunker", "fuel"}
_TUG = {"tug", "tug_supply", "supply"}
_CARGO = {"cargo", "bulk_carrier", "container", "general_cargo"}


def classify_pair(
    type_a: str,
    type_b: str,
) -> MeetingClass:
    """Classify the nature of an encounter from the two vessel types.

    Priority order: TRANSSHIPMENT > BUNKERING > FISHING_COORD > PORT_ASSIST
    """
    ta = (type_a or "").lower().strip()
    tb = (type_b or "").lower().strip()

    # Count per-vessel (not per-type) so fishing+fishing isn't collapsed to 1
    fishing_count = (1 if ta in _FISHING else 0) + (1 if tb in _FISHING else 0)
    reefer_count = (1 if ta in _REEFER else 0) + (1 if tb in _REEFER else 0)
    tanker_count = (1 if ta in _TANKER else 0) + (1 if tb in _TANKER else 0)
    tug_count = (1 if ta in _TUG else 0) + (1 if tb in _TUG else 0)

    if fishing_count >= 1 and reefer_count >= 1:
        return MeetingClass.TRANSSHIPMENT

    if tanker_count >= 1:
        return MeetingClass.BUNKERING

    if fishing_count == 2:
        return MeetingClass.FISHING_COORD

    if tug_count >= 1 and (ta in _CARGO or tb in _CARGO):
        return MeetingClass.PORT_ASSIST

    if not ta or not tb or ta == "unknown" or tb == "unknown":
        return MeetingClass.UNKNOWN

    return MeetingClass.VESSEL_TO_VESSEL
