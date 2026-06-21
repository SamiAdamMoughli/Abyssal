"""Mission Radar - Geo-Logik.

Diese Datei kapselt die gesamte raeumliche Pruefung. Sie ist bewusst von der
Risk Engine getrennt: Die Engine arbeitet nur mit dem fertigen Boolean
`in_protected_area` und weiss nichts ueber Geometrie. Wer die Geo-Quelle
aendert (andere GeoJSON, echte WDPA-Daten), fasst nur diese Datei an.

Die Schutzgebiets-Polygone werden EINMAL geladen (Modul-Level-Cache) und bei
jedem `is_in_protected_area()`-Aufruf wiederverwendet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import geopandas as gpd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

# Pfad zur lokalen GeoJSON mit den Schutzgebieten.
# backend/app/geo.py -> backend/data/protected_areas.geojson
DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "protected_areas.geojson"

# Modul-Level-Cache. Die zusammengefuehrte Geometrie aller Schutzgebiete wird
# beim ersten Zugriff genau einmal berechnet und danach wiederverwendet.
# Sentinel `_UNLOADED` unterscheidet "noch nicht geladen" von "geladen, leer".
_UNLOADED = object()
_protected_geom: object = _UNLOADED


def _load_protected_geometry() -> Optional[BaseGeometry]:
    """Laedt alle Schutzgebiets-Polygone und fuehrt sie zu einer Geometrie zusammen.

    Wird nur einmal real ausgefuehrt; danach kommt das Ergebnis aus dem Cache.
    Fehlt die Datei, gibt es keine Schutzgebiete -> None (kein Schiff liegt drin).
    """
    global _protected_geom
    if _protected_geom is not _UNLOADED:
        return _protected_geom  # type: ignore[return-value]

    if not DATA_PATH.exists():
        # Ohne Datei gibt es keine Schutzgebiete. Bewusst kein Crash: das System
        # laeuft weiter, meldet eben fuer alle Schiffe "ausserhalb".
        _protected_geom = None
        return None

    gdf = gpd.read_file(DATA_PATH)
    if gdf.empty:
        _protected_geom = None
        return None

    # Alle Polygone zu einer einzigen Geometrie verschmelzen - so ist die
    # Punkt-Pruefung ein einziger Aufruf, unabhaengig von der Anzahl Gebiete.
    _protected_geom = unary_union(gdf.geometry.values)
    return _protected_geom  # type: ignore[return-value]


def is_in_protected_area(lat: float, lon: float) -> bool:
    """True, wenn die Position (lat, lon) in einem Schutzgebiet liegt.

    Achtung Reihenfolge: GeoJSON/shapely arbeiten mit (x=lon, y=lat).
    """
    geom = _load_protected_geometry()
    if geom is None:
        return False
    point = Point(lon, lat)
    # covers schliesst den Rand mit ein (Punkt exakt auf der Grenze zaehlt als drin).
    return bool(geom.covers(point))


def reset_cache() -> None:
    """Verwirft den Cache - vor allem fuer Tests, falls die GeoJSON wechselt."""
    global _protected_geom
    _protected_geom = _UNLOADED
