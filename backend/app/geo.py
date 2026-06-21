"""Mission Radar - Geo-Logik.

Diese Datei kapselt die gesamte raeumliche Pruefung. Sie ist bewusst von der
Risk Engine getrennt: Die Engine arbeitet nur mit dem fertigen Boolean
`in_protected_area` und weiss nichts ueber Geometrie. Wer die Geo-Quelle
aendert, fasst nur diese Datei an - die Engine bleibt unberuehrt.

Zwei Quellen, per Umgebungsvariable PROTECTED_AREA_SOURCE umschaltbar:
  - "local" : lokale Platzhalter-GeoJSON (backend/data/protected_areas.geojson)
  - "gfw"   : echte WDPA-Daten live ueber die Global Forest Watch Data API
              (gfw_data_api.fetch_protected_areas_geojson) fuer eine bbox

In beiden Faellen werden die Polygone EINMAL geladen (Modul-Level-Cache), zu
einer shapely-Geometrie verschmolzen und fuer alle Punktpruefungen wiederverwendet.
Die rohe FeatureCollection bleibt zusaetzlich erhalten - fuer den Karten-Layer.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv
from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# .env laden, bevor Umgebungsvariablen gelesen werden (Import-Reihenfolge-sicher).
load_dotenv()

# Pfad zur lokalen GeoJSON (Quelle "local").
# backend/app/geo.py -> backend/data/protected_areas.geojson
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "protected_areas.geojson"

# Welche Quelle? "local" (Default, laeuft ohne Key) oder "gfw" (echte WDPA-Daten).
# Lazy gelesen (in _load), damit ein spaeter gesetzter Wert noch greift.
def _source() -> str:
    return os.environ.get("PROTECTED_AREA_SOURCE", "local").lower()

# Interessensgebiet fuer die "gfw"-Quelle: nur Schutzgebiete in dieser bbox laden.
# (min_lon, min_lat, max_lon, max_lat) - Default grob Galapagos.
def _aoi_bbox() -> Tuple[float, float, float, float]:
    raw = os.environ.get("PROTECTED_AREA_BBOX", "-91.8,-1.5,-89.0,0.7")
    p = [float(x) for x in raw.split(",")]
    return (p[0], p[1], p[2], p[3])


# Modul-Level-Cache. Beides wird beim ersten Zugriff genau einmal befuellt.
_UNLOADED = object()
_protected_geom: object = _UNLOADED                      # shapely-Geometrie (Union)
_protected_fc: Optional[Dict[str, Any]] = None           # rohe FeatureCollection


def _build_geometry(features: List[Dict[str, Any]]) -> Optional[BaseGeometry]:
    """Verschmilzt die Geometrien einer Feature-Liste zu einer Geometrie."""
    geoms = []
    for feat in features:
        geom = feat.get("geometry")
        if geom:
            try:
                geoms.append(shape(geom))
            except (ValueError, AttributeError):
                continue  # defekte Geometrie ueberspringen statt crashen
    if not geoms:
        return None
    return unary_union(geoms)


def _load() -> Tuple[Optional[BaseGeometry], Dict[str, Any]]:
    """Laedt Schutzgebiete aus der konfigurierten Quelle (einmalig, gecacht)."""
    global _protected_geom, _protected_fc
    if _protected_geom is not _UNLOADED:
        return _protected_geom, (_protected_fc or {"type": "FeatureCollection", "features": []})  # type: ignore[return-value]

    if _source() == "gfw":
        # Echte WDPA-Daten live. Import lokal, damit "local" ohne die GFW-Kette laeuft.
        from .gfw_data_api import fetch_protected_areas_geojson
        fc = fetch_protected_areas_geojson(_aoi_bbox())
    else:
        # Lokale Platzhalter-GeoJSON. Fehlt sie, gibt es eben keine Schutzgebiete.
        if not DATA_PATH.exists():
            fc = {"type": "FeatureCollection", "features": []}
        else:
            with open(DATA_PATH, "r", encoding="utf-8") as fh:
                fc = json.load(fh)

    _protected_fc = fc
    _protected_geom = _build_geometry(fc.get("features", []))
    return _protected_geom, fc  # type: ignore[return-value]


def is_in_protected_area(lat: float, lon: float) -> bool:
    """True, wenn die Position (lat, lon) in einem Schutzgebiet liegt.

    Achtung Reihenfolge: GeoJSON/shapely arbeiten mit (x=lon, y=lat).
    """
    geom, _ = _load()
    if geom is None:
        return False
    return bool(geom.covers(Point(lon, lat)))


def get_protected_areas_geojson() -> Dict[str, Any]:
    """Liefert die geladene FeatureCollection - fuer den Karten-Layer im Frontend."""
    _load()
    return _protected_fc or {"type": "FeatureCollection", "features": []}


def reset_cache() -> None:
    """Verwirft den Cache - vor allem fuer Tests/Quellenwechsel."""
    global _protected_geom, _protected_fc
    _protected_geom = _UNLOADED
    _protected_fc = None
