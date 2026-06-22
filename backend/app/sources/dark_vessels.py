"""ASYNC-Quelle (GERUEST): "Dark Vessel"-Erkennung via Sentinel-1 SAR / VIIRS.

==========================================================================
WICHTIG: ASYNC WORKER - NIEMALS IM REQUEST-PFAD.
==========================================================================
SAR-Verarbeitung ist schwer und langsam (Szenen-Download, Detektion, Matching
gegen AIS). Sie gehoert in einen separaten Background-Worker; das Ergebnis
("dark vessels": Radar-Detektionen OHNE passendes AIS) wird gecacht. Der
Request-Pfad liest - wie bei den statischen Quellen - NUR aus dem Cache.

Status: BEWUSST NICHT VOLL IMPLEMENTIERT. Dieses Modul liefert die Struktur und
dokumentiert den Ablauf. Echte SAR-Pipeline = eigene Phase (rechen-/datenintensiv).

Quellen / Lizenz (frei, offiziell):
  - Copernicus Sentinel-1 SAR (ESA/EU) - offene Lizenz. Zugriff via Copernicus
    Data Space Ecosystem (https://dataspace.copernicus.eu).
  - VIIRS Nighttime Lights / Boat Detection (NASA Earthdata / NOAA) - offen,
    Login erforderlich (https://www.earthdata.nasa.gov).

So WUERDE der Worker laufen (Pseudo-Ablauf):
  1. Fuer eine bbox + Datum die passende(n) Sentinel-1-Szene(n) finden/holen.
  2. SAR-Detektor ueber die Szene laufen lassen -> Liste von Schiffs-Detektionen.
  3. Detektionen gegen zeitnahe AIS-Positionen (GFW) matchen.
  4. Detektionen OHNE AIS-Match = "dark vessels" -> mit Position/Zeit cachen.
  5. Request-Pfad liest die gecachten dark-vessel-Punkte (read_cache), nie live.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..data_cache import read_cache, write_cache

SOURCE = "dark_vessels"
BBox = Tuple[float, float, float, float]


def get_cached_dark_vessels() -> List[Dict[str, Any]]:
    """Request-Pfad: liest gecachte dark-vessel-Detektionen (oder leer).

    Reiner Cache-Lookup. Solange der Worker nichts produziert hat -> leere Liste.
    """
    data = read_cache(SOURCE)
    return data if isinstance(data, list) else []


def run_worker(bbox: BBox, date: str) -> Dict[str, Any]:
    """ASYNC Background-Worker (GERUEST) - NICHT aus einem Request aufrufen.

    Echte Implementierung folgt dem Pseudo-Ablauf im Modul-Docstring. Aktuell
    bewusst ein No-op-Stub, der klar macht, dass hier noch nichts rechnet.
    """
    raise NotImplementedError(
        "SAR/VIIRS dark-vessel worker ist ein Geruest - eigene Phase. "
        "Siehe Modul-Docstring fuer den geplanten Ablauf."
    )


def _seed_empty_cache() -> None:
    """Legt einen leeren Cache an, damit der Request-Pfad konsistent liest."""
    if read_cache(SOURCE) is None:
        write_cache(SOURCE, [])
