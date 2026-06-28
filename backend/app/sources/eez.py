"""STATISCHE Quelle: Exclusive Economic Zones (EEZ), Cache-only im Request-Pfad.

Quelle / Lizenz:
  - Marine Regions (https://www.marineregions.org) - "Maritime Boundaries and EEZ"
    (Flanders Marine Institute, VLIZ). Frei nutzbar mit Attribution (CC-BY).
    GeoJSON/Shapefile-Download der World EEZ.

EHRLICH: Der vollstaendige World-EEZ-Datensatz ist gross; in diesem Schritt wird
KEINE EEZ-Geometrie gebundelt (kein erfundener Platzhalter). Ohne lokale Datei
liefert die Quelle eine leere FeatureCollection -> eez_at() == None ->
rule_eez_violation feuert nie. Echte EEZ-GeoJSON via refresh_sources/Download
einspeisen (backend/data/sources/eez.geojson).

Aktualisierungsrhythmus: sehr statisch -> max_age 720h (~monatlich).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shapely.geometry import Point, shape

from ..data_cache import get_or_fetch

SOURCE = "eez"
MAX_AGE_H = 720.0
_LOCAL = (
    Path(__file__).resolve().parent.parent.parent / "data" / "sources" / "eez.geojson"
)

# In-Memory: Liste (sovereign_iso3, prepared_geometry) - lazy gebaut.
_polys: Optional[List[Tuple[str, Any]]] = None


def fetch_eez() -> Dict[str, Any]:
    """Laedt EEZ-GeoJSON (lokal, sonst leere FeatureCollection)."""
    if not _LOCAL.exists():
        return {"type": "FeatureCollection", "features": []}
    with open(_LOCAL, "r", encoding="utf-8") as fh:
        return json.load(fh)


def refresh() -> Dict[str, Any]:
    fc = get_or_fetch(SOURCE, fetch_eez, MAX_AGE_H, force=True)
    global _polys
    _polys = None
    return {"source": SOURCE, "features": len(fc.get("features", []))}


def _build() -> List[Tuple[str, Any]]:
    fc = get_or_fetch(SOURCE, fetch_eez, MAX_AGE_H)
    out = []
    for f in fc.get("features", []):
        props = f.get("properties", {})
        # MarineRegions nutzt u. a. "ISO_TER1"/"SOVEREIGN1"; defensiv lesen.
        iso = props.get("ISO_TER1") or props.get("ISO_SOV1") or props.get("iso3") or "?"
        geom = f.get("geometry")
        if geom:
            try:
                out.append((iso, shape(geom)))
            except (ValueError, AttributeError):
                continue
    return out


def warmup() -> None:
    global _polys
    _polys = _build()


def eez_at(lat: float, lon: float) -> Optional[str]:
    """Cache-only: ISO3 des EEZ-Kuestenstaats an dieser Position (oder None)."""
    global _polys
    if _polys is None:
        _polys = _build()
    if not _polys:
        return None
    p = Point(lon, lat)
    for iso, geom in _polys:
        if geom.covers(p):
            return iso
    return None
