"""STATISCHE Quelle: Sanktionslisten (vessel-relevanter Teil), Cache-only.

Quelle / Lizenz:
  - OpenSanctions (https://www.opensanctions.org) - konsolidiert offizielle
    Sanktionslisten (OFAC, EU, UN, UK ...). Daten unter offener Lizenz (CC-BY 4.0
    fuer den nicht-kommerziellen Bulk-Export). Vessel-Entitaeten (schema "Vessel")
    enthalten Name/IMO/MMSI/Flag.
  - Realer fetch: OpenSanctions Bulk-/Dataset-API, gefiltert auf schema=Vessel.

EHRLICH: In diesem Schritt ist KEINE Sanktionsliste gebundelt (keine erfundenen
Eintraege). Liegt keine lokale Datei vor, liefert die Quelle eine LEERE Liste ->
rule_sanctions_hit feuert nie. So bleibt der synthetic-Modus ohne Token/Token-
freie Quellen voll funktionsfaehig. Echte Daten via refresh_sources einspeisen.

Aktualisierungsrhythmus: statisch -> max_age 24h.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..data_cache import get_or_fetch

SOURCE = "sanctions"
MAX_AGE_H = 24.0
# Optionaler lokaler Roh-Export (falls vorhanden); sonst leer.
_LOCAL = Path(__file__).resolve().parent.parent.parent / "data" / "sources" / "sanctions_vessels.json"

_index: Optional[Dict[str, Any]] = None


def _norm(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def fetch_sanctions() -> List[Dict[str, Any]]:
    """Laedt sanktionierte Schiffe (lokaler Export, sonst leer - kein Erfinden)."""
    if not _LOCAL.exists():
        return []
    with open(_LOCAL, "r", encoding="utf-8") as fh:
        return json.load(fh)


def refresh() -> Dict[str, Any]:
    data = get_or_fetch(SOURCE, fetch_sanctions, MAX_AGE_H, force=True)
    global _index
    _index = None
    return {"source": SOURCE, "entries": len(data)}


def _build_index() -> Dict[str, Any]:
    data = get_or_fetch(SOURCE, fetch_sanctions, MAX_AGE_H)
    imo, mmsi, names = set(), set(), set()
    for e in data:
        if e.get("imo"):
            imo.add(str(e["imo"]).strip())
        if e.get("mmsi"):
            mmsi.add(str(e["mmsi"]).strip())
        n = _norm(e.get("name"))
        if n:
            names.add(n)
    return {"imo": imo, "mmsi": mmsi, "names": names, "count": len(data)}


def warmup() -> None:
    global _index
    _index = _build_index()


def lookup(mmsi: Optional[str], imo: Optional[str],
           name: Optional[str]) -> Optional[Dict[str, Any]]:
    """Cache-only Lookup: steht das Schiff auf einer Sanktionsliste?"""
    global _index
    if _index is None:
        _index = _build_index()
    if imo and str(imo).strip() in _index["imo"]:
        return {"match": "imo"}
    if mmsi and str(mmsi).strip() in _index["mmsi"]:
        return {"match": "mmsi"}
    n = _norm(name)
    if n and n in _index["names"]:
        return {"match": "name"}
    return None
