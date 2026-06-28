"""OBIS biodiversity context, cached for vessel-risk scoring.

Source / License:
  - OBIS (Ocean Biodiversity Information System), UNESCO/IOC.
    Public REST API, no API key required: https://api.obis.org/

Design:
  - Background/refresh path may query OBIS for a bounded area.
  - Request/scoring path performs cache-only point lookups around vessel
    positions. No live network call is made while scoring vessels.

This is deliberately an area cache, not a global mirror. Set BIODIVERSITY_BBOX
or reuse GFW_BBOX/PROTECTED_AREA_BBOX for the current patrol region.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from ..data_cache import get_or_fetch, read_cache

SOURCE = "biodiversity_obis"
MAX_AGE_H = 168.0
OBIS_BASE = os.environ.get("OBIS_API_BASE", "https://api.obis.org/v3")
HTTP_TIMEOUT_SECONDS = float(os.environ.get("OBIS_LOOKUP_RADIUS_DEG", "0.1"))

logger = logging.getLogger("mission_radar.biodiversity")

BBox = Tuple[float, float, float, float]

_records: Optional[List[Dict[str, Any]]] = None


# Taxonomic target keywords for classification matching
_CETACEAN_KEYWORDS = {
    "balaenoptera",
    "delphinus",
    "globicephala",
    "kogia",
    "megaptera",
    "orcinus",
    "physeter",
    "stenella",
    "tursiops",
    "ziphius",
}
_TURTLE_KEYWORDS = {
    "caretta",
    "chelonia",
    "dermochelys",
    "eretmochelys",
    "lepidochelys",
    "natator",
}
_SHARK_RAY_KEYWORDS = {
    "alopias",
    "carcharhinus",
    "carcharias",
    "isurus",
    "manta",
    "mobula",
    "pristis",
    "rhincodon",
    "sphyrna",
}
_SEABIRD_KEYWORDS = {
    "diomedea",
    "thalassarche",
    "procellaria",
    "phoebastria",
}
_PINNIPED_MAMMAL_KEYWORDS = {
    "dugong",
    "trichechus",
    "zalophus",
    "arctocephalus",
    "enhydra",
}
_PELAGIC_FISH_KEYWORDS = {
    "thunnus",
    "makaira",
    "xiphias",
    "epinephelus",
}
_THREATENED_CATEGORIES = {"CR", "EN", "VU"}


def _configured_bbox() -> BBox:
    raw = (
        os.environ.get("BIODIVERSITY_BBOX")
        or os.environ.get("GFW_BBOX")
        or os.environ.get("PROTECTED_AREA_BBOX")
        or "-91.8,-1.5,-89.0,0.7"
    )
    try:
        min_lon, min_lat, max_lon, max_lat = [float(v) for v in raw.split(",")]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid BIODIVERSITY_BBOX parameter configured: {raw!r}. "
            "Expected format: 'min_lon,min_lat,max_lon,max_lat'."
        ) from exc
    if min_lon >= max_lon or min_lat >= max_lat:
        raise ValueError(
            "Invalid BIODIVERSITY_BBOX boundaries: min values must be less than max values."
        )
    return min_lon, min_lat, max_lon, max_lat


def _bbox_polygon_wkt(bbox: BBox) -> str:
    min_lon, min_lat, max_lon, max_lat = bbox
    return (
        "POLYGON(("
        f"{min_lon} {min_lat}, {max_lon} {min_lat}, {max_lon} {max_lat}, "
        f"{min_lon} {max_lat}, {min_lon} {min_lat}"
        "))"
    )


def _record_lat_lon(record: Dict[str, Any]) -> tuple[Optional[float], Optional[float]]:
    lat = record.get("decimalLatitude") or record.get("lat")
    lon = record.get("decimalLongitude") or record.get("lon")
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def _label(record: Dict[str, Any]) -> str:
    return str(
        record.get("scientificName")
        or record.get("species")
        or record.get("acceptedScientificName")
        or "Unknown species"
    )


def _iucn_category(record: Dict[str, Any]) -> str:
    for key in ("redlistCategory", "iucnRedListCategory", "category"):
        value = record.get(key)
        if value:
            return str(value).upper()
    return ""


def classify_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize raw OBIS occurrence records into risk-relevant buckets."""
    record_list = list(records)
    unique_species: set[str] = set()
    cetaceans: list[str] = []
    turtles: list[str] = []
    sharks_rays: list[str] = []
    seabirds: list[str] = []
    pinnipeds: list[str] = []
    pelagic_fish: list[str] = []
    threatened: list[str] = []

    def add_once(bucket: list[str], name: str) -> None:
        if name not in bucket:
            bucket.append(name)

    for rec in record_list:
        name = _label(rec)
        low = name.lower()
        unique_species.add(name)

        if any(k in low for k in _CETACEAN_KEYWORDS):
            add_once(cetaceans, name)
        if any(k in low for k in _TURTLE_KEYWORDS):
            add_once(turtles, name)
        if any(k in low for k in _SHARK_RAY_KEYWORDS):
            add_once(sharks_rays, name)
        if any(k in low for k in _SEABIRD_KEYWORDS):
            add_once(seabirds, name)
        if any(k in low for k in _PINNIPED_MAMMAL_KEYWORDS):
            add_once(pinnipeds, name)
        if any(k in low for k in _PELAGIC_FISH_KEYWORDS):
            add_once(pelagic_fish, name)

        if _iucn_category(rec) in _THREATENED_CATEGORIES:
            add_once(threatened, name)

    return {
        "bio_risk": _bio_risk_level(
            cetaceans,
            turtles,
            sharks_rays,
            seabirds,
            pinnipeds,
            threatened,
            pelagic_fish,
        ),
        "total_records": len(record_list),
        "total_species": len(unique_species),
        "cetaceans": cetaceans[:8],
        "sea_turtles": turtles[:8],
        "sharks_rays": sharks_rays[:8],
        "seabirds": seabirds[:8],
        "pinnipeds": pinnipeds[:8],
        "pelagic_fish": pelagic_fish[:8],
        "threatened_species": threatened[:8],
    }


def _bio_risk_level(
    cetaceans: list[str],
    turtles: list[str],
    sharks_rays: list[str],
    seabirds: list[str],
    pinnipeds: list[str],
    threatened: list[str],
    pelagic_fish: list[str],
) -> str:
    # High Risk: Critical, easily targeted endangered marine megafauna present
    if cetaceans or turtles or len(threatened) >= 3:
        return "high"
    # Medium Risk: Kept sharks/rays, sensitive coastal/avian vectors, or isolated endangered elements
    if sharks_rays or pinnipeds or seabirds or threatened:
        return "medium"
    # Low Risk: Standard targeted commercial/pelagic fish species presence detected
    if pelagic_fish:
        return "low"
    return "none"


def fetch_area_species(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    *,
    limit: int = MAX_RECORDS,
) -> List[Dict[str, Any]]:
    """Fetch OBIS occurrence records inside a bbox."""
    bbox = (min_lon, min_lat, max_lon, max_lat)
    response = requests.get(
        f"{OBIS_BASE}/occurrence",
        params={"geometry": _bbox_polygon_wkt(bbox), "size": limit},
        timeout=HTTP_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results", []) if isinstance(payload, dict) else []
    compact: List[Dict[str, Any]] = []
    for rec in results:
        lat, lon = _record_lat_lon(rec)
        if lat is None or lon is None:
            continue
        compact.append(
            {
                "scientificName": _label(rec),
                "decimalLatitude": lat,
                "decimalLongitude": lon,
                "redlistCategory": _iucn_category(rec),
                "eventDate": rec.get("eventDate"),
                "depth": rec.get("depth") or rec.get("minimumDepthInMeters"),
            }
        )
    return compact


def fetch_configured_area() -> List[Dict[str, Any]]:
    min_lon, min_lat, max_lon, max_lat = _configured_bbox()
    return fetch_area_species(min_lat, max_lat, min_lon, max_lon)


def refresh() -> Dict[str, Any]:
    global _records
    data = get_or_fetch(SOURCE, fetch_configured_area, MAX_AGE_H, force=True)
    _records = None  # Flushes the active warm runtime memory cache
    summary = classify_records(data)
    return {
        "source": SOURCE,
        "records": len(data),
        "species": summary["total_species"],
        "bio_risk": summary["bio_risk"],
    }


def _load_cached_records() -> List[Dict[str, Any]]:
    cached = read_cache(SOURCE)
    return cached if isinstance(cached, list) else []


def warmup() -> None:
    global _records
    _records = _load_cached_records()


def lookup(
    lat: float,
    lon: float,
    radius_deg: float = DEFAULT_LOOKUP_RADIUS_DEG,
) -> Dict[str, Any]:
    """Cache-only species context around an exact coordinate point."""
    global _records
    if _records is None:
        _records = _load_cached_records()

    nearby = []
    for rec in _records:
        rlat = rec.get("decimalLatitude")
        rlon = rec.get("decimalLongitude")
        if rlat is None or rlon is None:
            continue
        if abs(rlat - lat) <= radius_deg and abs(rlon - lon) <= radius_deg:
            nearby.append(rec)
    return classify_records(nearby)


def enrich_vessels(vessels: Iterable[Any]) -> None:
    """Attach compiled biodiversity context properties onto Vessel instances in place."""
    for vessel in vessels:
        context = lookup(vessel.lat, vessel.lon)
        vessel.bio_risk = context["bio_risk"]
        vessel.bio_species_count = context["total_species"]
        vessel.bio_threatened_species = context["threatened_species"]
        vessel.bio_cetaceans = context["cetaceans"]
        vessel.bio_sea_turtles = context["sea_turtles"]
        vessel.bio_sharks_rays = context["sharks_rays"]
        vessel.bio_seabirds = context["seabirds"]
        vessel.bio_pinnipeds = context["pinnipeds"]
        vessel.bio_pelagic_fish = context["pelagic_fish"]
