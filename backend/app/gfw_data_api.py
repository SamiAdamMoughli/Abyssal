"""Mission Radar - Global FOREST Watch Data API (Schutzgebiete / WDPA).

ACHTUNG NAMENS-VERWECHSLUNG:
Dieses Modul spricht die "GFW DATA API" v0.3.0 an - das ist *Global Forest Watch*
(Wald-, Raster-, Vektordaten + Schutzgebiete), NICHT *Global Fishing Watch*.
Diese API liefert KEINE Schiffspositionen/AIS. Schiffsdaten kommen aus einer
getrennten Quelle (siehe gfw_vessels.py / AIS-Provider). Dieses Modul wird hier
ausschliesslich genutzt, um echte WDPA-Schutzgebiets-Geometrien fuer
`is_in_protected_area` abzufragen.

Auth (laut Spec, securitySchemes):
  - API-Key im Header ODER Query-Param "x-api-key" (APIKeyOriginHeader/Query).
  - Zusaetzlich ein "origin"-Header, der zur Key-Allowlist passt
    (APIKeyRequestIn.domains). Beides kommt aus Umgebungsvariablen, nie aus Code.

Doku / Key-Beschaffung:
  - Datasets durchsuchen:  GET /datasets   (keine Auth noetig)
  - Key anlegen:           POST /auth/sign-up  ->  POST /auth/apikey
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Tuple

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("mission_radar.gfw_data_api")

# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #

# Basis-URL der GFW Data API. Die OpenAPI-Spec listet keine `servers` -> PRUEFEN.
# Default ist der dokumentierte Produktions-Host der Global Forest Watch Data API.
GFW_DATA_API_BASE = os.environ.get(
    "GFW_DATA_API_BASE", "https://data-api.globalforestwatch.org"
)

# Netzwerk-Timeout (Sekunden). Lieber knapp + klare Fehlermeldung als haengen.
HTTP_TIMEOUT_SECONDS = float(os.environ.get("GFW_HTTP_TIMEOUT", "30"))

# ========================================================================== #
# Verifiziert gegen die Live-API (Stand 2026-06) via list_datasets() und
# GET /dataset/wdpa_protected_areas/v202512/fields:
#   - Dataset "wdpa_protected_areas" existiert (globales WDPA, Polygone)
#   - Geometrie-Spalte heisst "geom"
#   - Punkt-in-Polygon-Query liefert plausible Ergebnisse (Galapagos-Reserve=True,
#     offener Pazifik=False)
#   - Re-bestaetigt via GET /datasets (x-api-key): "wdpa_protected_areas" ist der
#     Polygon-Slug; "wdpa_protected_areas__..." sind abgeleitete Alert-/Summary-
#     Layer (NICHT die Schutzgebiets-Geometrie).
# Version: GFW veroeffentlicht periodisch neue Versionen (vYYYYMM...). Aktuellste
# mit list-versions ermitteln (GET /dataset/wdpa_protected_areas) und ggf. bumpen.
WDPA_DATASET = os.environ.get("GFW_WDPA_DATASET", "wdpa_protected_areas")
WDPA_VERSION = os.environ.get("GFW_WDPA_VERSION", "v202512")
WDPA_GEOM_COLUMN = os.environ.get("GFW_WDPA_GEOM_COLUMN", "geom")


class GfwDataApiError(RuntimeError):
    """Wird bei jedem Problem mit der GFW Data API geworfen - keine stillen Fehler."""


# --------------------------------------------------------------------------- #
# Auth-Header
# --------------------------------------------------------------------------- #


def _auth_headers(require_key: bool = True) -> Dict[str, str]:
    """Baut die Auth-Header: x-api-key + origin (beide aus der Umgebung).

    require_key=False fuer Endpunkte ohne Auth (z. B. GET /datasets).
    """
    headers: Dict[str, str] = {"Accept": "application/json"}

    key = os.environ.get("GFW_API_KEY")
    if key:
        # Key per Header (APIKeyOriginHeader). Origin muss zur Allowlist passen.
        headers["x-api-key"] = key
        origin = os.environ.get("GFW_API_ORIGIN")
        if origin:
            headers["origin"] = origin
    elif require_key:
        raise GfwDataApiError(
            "GFW_API_KEY ist nicht gesetzt. Lege einen Key an "
            "(POST /auth/sign-up -> POST /auth/apikey) und setze GFW_API_KEY "
            "sowie GFW_API_ORIGIN (passend zur domains-Allowlist) - siehe "
            ".env.example."
        )
    return headers


# --------------------------------------------------------------------------- #
# Robuste HTTP-Schicht
# --------------------------------------------------------------------------- #


def _get(path: str, params: Dict[str, Any], require_key: bool = True) -> Dict[str, Any]:
    """Fuehrt einen GET-Request aus und gibt das JSON zurueck.

    Robustheit: Timeout, Netzwerk-/HTTP-Fehler werden zu einer klaren
    GfwDataApiError. 422 (Validation Error laut Spec) wird gesondert geloggt, da
    es fast immer auf falsches SQL/Parameter hindeutet.
    """
    url = f"{GFW_DATA_API_BASE}{path}"
    headers = _auth_headers(require_key=require_key)

    try:
        response = requests.get(
            url, headers=headers, params=params, timeout=HTTP_TIMEOUT_SECONDS
        )
    except requests.exceptions.Timeout as exc:
        raise GfwDataApiError(
            f"GFW Data API nach {HTTP_TIMEOUT_SECONDS}s ohne Antwort (Timeout): {url}"
        ) from exc
    except requests.exceptions.RequestException as exc:
        raise GfwDataApiError(f"GFW Data API nicht erreichbar: {exc}") from exc

    if response.status_code == 401:
        raise GfwDataApiError(
            "GFW Data API meldet 401 Unauthorized - x-api-key fehlt/ungueltig oder "
            "der origin-Header passt nicht zur domains-Allowlist des Keys."
        )
    if response.status_code == 422:
        # Validation Error: meist falsches SQL oder falsche Parameter. Gesondert
        # loggen, damit die Ursache sichtbar wird.
        body = response.text[:500]
        logger.error("GFW Data API 422 Validation Error fuer %s: %s", url, body)
        raise GfwDataApiError(
            "GFW Data API 422 (Validation Error) - vermutlich fehlerhaftes SQL "
            f"oder ungueltige Parameter. Antwort: {body}"
        )
    if not response.ok:
        snippet = response.text[:300]
        raise GfwDataApiError(
            f"GFW Data API antwortete mit HTTP {response.status_code}: {snippet}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise GfwDataApiError("GFW Data API lieferte keine gueltige JSON-Antwort.") from exc


# --------------------------------------------------------------------------- #
# list_datasets - Hilfsfunktion, um den echten WDPA-Dataset-Namen zu finden
# --------------------------------------------------------------------------- #


def list_datasets(page_size: int = 100) -> List[Dict[str, Any]]:
    """Ruft GET /datasets auf (keine Auth noetig) und gibt die Dataset-Liste zurueck.

    Nutze das, um den korrekten dataset-Namen/-Version fuer WDPA-Polygone zu
    finden und ihn dann in WDPA_DATASET / WDPA_VERSION oben einzutragen.
    """
    payload = _get(
        "/datasets",
        params={"page[size]": page_size},
        require_key=False,
    )
    # Antwort ist typischerweise {"data": [...]} (JSON:API-Stil). Defensiv parsen.
    data = payload.get("data", payload)
    if not isinstance(data, list):
        raise GfwDataApiError(
            "Unerwartetes Format von GET /datasets - keine Liste gefunden."
        )
    return data


# --------------------------------------------------------------------------- #
# Schutzgebiets-Polygone fuer eine bbox holen (fuer Karte + lokale Punktpruefung)
# --------------------------------------------------------------------------- #
# Verifiziert gegen die Live-API: die SQL-Funktionen ST_MakeEnvelope,
# ST_Intersects und ST_AsGeoJSON werden unterstuetzt.


def fetch_protected_areas_geojson(bbox: Tuple[float, float, float, float]) -> Dict[str, Any]:
    """Holt echte WDPA-Schutzgebiete, die eine bbox schneiden, als GeoJSON.

    bbox = (min_lon, min_lat, max_lon, max_lat). Rueckgabe ist eine GeoJSON
    FeatureCollection mit den Properties name + iucn_cat - geeignet sowohl als
    Karten-Layer als auch als Eingabe fuer die lokale shapely-Punktpruefung.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    envelope = f"ST_MakeEnvelope({min_lon},{min_lat},{max_lon},{max_lat},4326)"
    # Felder verifiziert via GET /dataset/.../fields: name, iucn_cat, gis_area
    # (km²), site_id (WDPA-Kennung).
    sql = (
        f"SELECT name, iucn_cat, gis_area, site_id, "
        f"ST_AsGeoJSON({WDPA_GEOM_COLUMN}) AS geojson "
        f"FROM data WHERE ST_Intersects({WDPA_GEOM_COLUMN}, {envelope})"
    )
    path = f"/dataset/{WDPA_DATASET}/{WDPA_VERSION}/query/json"
    payload = _get(path, params={"sql": sql})
    rows = payload.get("data", payload)
    if not isinstance(rows, list):
        raise GfwDataApiError("Unerwartetes Query-Format - keine Zeilenliste.")

    features: List[Dict[str, Any]] = []
    for row in rows:
        raw = row.get("geojson")
        if not raw:
            continue
        try:
            geometry = json.loads(raw)
        except (TypeError, ValueError):
            continue  # defekte Geometrie ueberspringen statt crashen
        area = row.get("gis_area")
        features.append({
            "type": "Feature",
            "properties": {
                "name": row.get("name"),
                "iucn_cat": row.get("iucn_cat"),
                "area_km2": round(float(area)) if area is not None else None,
                "wdpa_id": row.get("site_id"),
            },
            "geometry": geometry,
        })
    return {"type": "FeatureCollection", "features": features}


# --------------------------------------------------------------------------- #
# is_in_protected_area - echte WDPA-Abfrage per SQL
# --------------------------------------------------------------------------- #


def _build_point_in_protected_sql(lat: float, lon: float) -> str:
    """Baut das SQL fuer eine Punkt-in-Schutzgebiet-Abfrage.

    ====================================================================== #
    >>> An echtes WDPA-Dataset/-Schema anpassen <<<
    ====================================================================== #
    Spaltennamen (WDPA_GEOM_COLUMN) und ggf. die raeumliche Funktion haengen vom
    Dataset ab. ST_Intersects/ST_Point sind ueblich; verifiziere die unterstuetzte
    SQL-Syntax in der Doku bzw. ueber GET /dataset/{dataset}/{version}/fields.
    Hinweis: GeoJSON/PostGIS nutzen die Reihenfolge (lon, lat).
    """
    return (
        f"SELECT 1 FROM data "
        f"WHERE ST_Intersects({WDPA_GEOM_COLUMN}, ST_SetSRID(ST_Point({lon}, {lat}), 4326)) "
        f"LIMIT 1"
    )


def is_in_protected_area(lat: float, lon: float) -> bool:
    """True, wenn (lat, lon) laut WDPA-Daten der GFW Data API in einem Schutzgebiet liegt.

    Fragt /dataset/{dataset}/{version}/query/json mit einem Punkt-SQL ab. Liefert
    die Query mindestens eine Zeile, liegt der Punkt in einem Schutzgebiet.

    Hinweis: Dies ist die ALTERNATIVE zu der lokalen GeoJSON-Pruefung in geo.py.
    Welche Quelle is_in_protected_area letztlich speist, entscheidest du beim
    Verdrahten - die Risk Engine merkt davon nichts.
    """
    sql = _build_point_in_protected_sql(lat, lon)
    path = f"/dataset/{WDPA_DATASET}/{WDPA_VERSION}/query/json"
    payload = _get(path, params={"sql": sql})

    # Antwort ist typischerweise {"data": [...]} - mind. eine Zeile => im Gebiet.
    data = payload.get("data", payload)
    if isinstance(data, list):
        return len(data) > 0
    raise GfwDataApiError(
        "Unerwartetes Antwortformat der Query - konnte Ergebniszeilen nicht lesen."
    )
