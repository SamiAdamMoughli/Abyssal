"""OBIS API — marine species presence per H3 cell.

Ocean Biodiversity Information System (UNESCO/IOC).
Free, no API key required.
Docs: https://api.obis.org/

Strategy: for each H3 cell we query the OBIS checklist endpoint for
species within a radius that covers the cell (~5 km at resolution 7).
We then classify the result into risk-relevant categories:
  - cetaceans    (whales, dolphins)
  - threatened   (IUCN Red List: EN / CR / VU)
  - sharks_rays  (elasmobranchs)
  - sea_turtles

This gives the frontend the data to show a "bio-risk" overlay and
gives the risk engine context for sanctuary + species flags.
"""

from __future__ import annotations

import asyncio
from typing import Any

import h3 as _h3
import httpx

BASE = "https://api.obis.org/v3"
TIMEOUT = 15.0

# H3 resolution 7 average edge length ≈ 1.2 km → radius 8 km covers the cell
SEARCH_RADIUS_KM = 8

# Taxon IDs / name fragments for high-value categories
_CETACEAN_KEYWORDS = {
    "balaenoptera", "physeter", "megaptera", "delphinus",
    "tursiops", "orcinus", "ziphius", "kogia",
}
_TURTLE_KEYWORDS = {
    "chelonia", "caretta", "eretmochelys", "lepidochelys",
    "dermochelys", "natator",
}
_SHARK_KEYWORDS = {
    "carcharhinus", "isurus", "alopias", "sphyrna",
    "rhincodon", "carcharias",
}
_THREATENED_CATEGORIES = {"EN", "CR", "VU"}


def _classify(species_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify a raw OBIS species list into risk-relevant buckets."""
    cetaceans: list[str] = []
    turtles:   list[str] = []
    sharks:    list[str] = []
    threatened: list[str] = []

    for sp in species_list:
        name = str(sp.get("scientificName") or sp.get("species") or "").lower()
        label = sp.get("vernacularName") or sp.get("species") or name
        category = str(sp.get("category") or "").upper()

        if any(k in name for k in _CETACEAN_KEYWORDS):
            cetaceans.append(label)
        if any(k in name for k in _TURTLE_KEYWORDS):
            turtles.append(label)
        if any(k in name for k in _SHARK_KEYWORDS):
            sharks.append(label)
        if category in _THREATENED_CATEGORIES:
            threatened.append(label)

    return {
        "cetaceans":          cetaceans[:5],
        "sea_turtles":        turtles[:5],
        "sharks_rays":        sharks[:5],
        "threatened_species": threatened[:5],
        "total_species":      len(species_list),
        "bio_risk":           _bio_risk_level(cetaceans, turtles, sharks, threatened),
    }


def _bio_risk_level(
    cetaceans, turtles, sharks, threatened
) -> str:
    """Return 'high' | 'medium' | 'low' | 'none'."""
    if cetaceans or turtles or len(threatened) >= 3:
        return "high"
    if sharks or len(threatened) >= 1:
        return "medium"
    if len(cetaceans) + len(turtles) + len(sharks) + len(threatened) > 0:
        return "low"
    return "none"


async def _fetch_one(
    client: httpx.AsyncClient, cell_id: str
) -> dict[str, Any]:
    lat, lon = _h3.cell_to_latlng(cell_id)

    # checklist endpoint: unique species in a radius
    try:
        r = await client.get(f"{BASE}/checklist", params={
            "lat":    round(lat, 4),
            "lon":    round(lon, 4),
            "radius": SEARCH_RADIUS_KM,
            "limit":  50,
        })
        if r.status_code != 200:
            return _classify([])
        data = r.json()
    except Exception:
        return _classify([])

    species_list = data.get("results", []) if isinstance(data, dict) else []
    return _classify(species_list)


async def fetch_species_presence(
    cell_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Return {cell_id: species_context} for a list of H3 cells."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        results = await asyncio.gather(
            *[_fetch_one(client, cid) for cid in cell_ids],
            return_exceptions=True,
        )

    return {
        cid: (r if isinstance(r, dict) else _classify([]))
        for cid, r in zip(cell_ids, results)
    }
