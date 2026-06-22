"""STATISCHE Quelle: offizielle IUU-Schiffsliste (Cache-only im Request-Pfad).

Quelle / Lizenz:
  - CCAMLR Non-Contracting Party IUU Vessel List (oeffentlich, behoerdlich).
    https://www.ccamlr.org/en/compliance/non-contracting-party-iuu-vessel-list
  - Zielbild: TMT "Combined IUU Vessel List" (iuu-vessels.org) konsolidiert alle
    RFMO-Listen - dort wuerde der echte fetch ansetzen.
Aktuell bundelt das Projekt die verifizierten CCAMLR-Eintraege lokal
(backend/data/iuu_official.json). Der fetch liest diese Datei; ein realer
Online-fetch (TMT) ersetzt spaeter nur fetch_iuu().

Aktualisierungsrhythmus: statisch -> max_age 24h, Refresh per Hintergrund-Job.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..data_cache import get_or_fetch, read_cache

SOURCE = "iuu_list"
MAX_AGE_H = 24.0
_LOCAL = Path(__file__).resolve().parent.parent.parent / "data" / "iuu_official.json"

# In-Memory-Index (lazy gebaut). Request-Pfad nutzt NUR diesen Lookup.
_index: Optional[Dict[str, Any]] = None


def _norm(s: Optional[str]) -> str:
    if not s:
        return ""
    s = re.sub(r"[^a-z0-9]+", " ", s.lower().strip())
    return re.sub(r"\s+", " ", s).strip()


def fetch_iuu() -> List[Dict[str, Any]]:
    """Laedt + normalisiert die offizielle IUU-Liste (hier: lokale CCAMLR-Datei)."""
    if not _LOCAL.exists():
        return []
    with open(_LOCAL, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out = []
    for v in raw.get("vessels", []):
        out.append({
            "name": v.get("name"),
            "aliases": v.get("aliases", []),
            "imo": v.get("imo"),
            "mmsi": v.get("mmsi"),
            "flag": v.get("flag", "Unknown"),
            "source": v.get("listing_source", "CCAMLR"),
            "year": v.get("listing_year"),
        })
    return out


def refresh() -> Dict[str, Any]:
    """Erzwingt einen Neu-Load in den Cache (fuer den Background-Job)."""
    data = get_or_fetch(SOURCE, fetch_iuu, MAX_AGE_H, force=True)
    global _index
    _index = None
    return {"source": SOURCE, "entries": len(data)}


def _build_index() -> Dict[str, Any]:
    data = get_or_fetch(SOURCE, fetch_iuu, MAX_AGE_H)
    imo, mmsi, names = set(), set(), {}
    for e in data:
        if e.get("imo"):
            imo.add(str(e["imo"]).strip())
        if e.get("mmsi"):
            mmsi.add(str(e["mmsi"]).strip())
        for nm in [e.get("name"), *e.get("aliases", [])]:
            n = _norm(nm)
            if n:
                names[n] = e
    return {"imo": imo, "mmsi": mmsi, "names": names, "count": len(data)}


def warmup() -> None:
    """Index einmal vorladen (beim App-Start), damit Requests rein In-Memory sind."""
    global _index
    _index = _build_index()


def lookup(mmsi: Optional[str], imo: Optional[str],
           name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Cache-only Lookup: liegt das Schiff auf der offiziellen IUU-Liste?"""
    global _index
    if _index is None:
        _index = _build_index()
    if imo and str(imo).strip() in _index["imo"]:
        return {"match": "imo", "entry": None}
    if mmsi and str(mmsi).strip() in _index["mmsi"]:
        return {"match": "mmsi", "entry": None}
    n = _norm(name)
    if n and n in _index["names"]:
        e = _index["names"][n]
        return {"match": "name", "entry": e}
    return None
