"""STATISCHE Quelle: OpenSanctions - sanktionierte SCHIFFE (Zone A).

# OpenSanctions - open data, CC BY 4.0 NonCommercial.
#   Projekt:    https://www.opensanctions.org
#   Doku:       https://www.opensanctions.org/docs/api/
#   Bulk:       https://data.opensanctions.org/datasets/latest/sanctions/
# Konsolidiert offizielle Sanktionslisten: US OFAC, EU, UN, UK, CH, CA, AU ...
# KEIN API-Token noetig fuer den Bulk-Download.

SCOPE (Zone A): NUR schema="Vessel" (Schiffe als Objekte). Der persons-Datensatz
wird NICHT angefasst - das waere Zone B (menschengefuehrtes Analysten-Modul).

ZWEI GETRENNTE RISIKO-DIMENSIONEN: Sanktionierte Schiffe (ueberwiegend Tanker:
Russland/Iran/Nordkorea, Oelschmuggel/Sanktionsumgehung) sind eine ANDERE
Population als IUU-Fischerei-Schiffe (CCAMLR/RFMO). Dieses Signal ERGAENZT das
IUU-Signal, es doppelt es nicht.

Performance: Der 345-MB-Bulk wird NUR im Background-Job (refresh_sources) bzw. in
load_sanctioned_vessels() geladen. warmup() und match_vessel() lesen ausschliesslich
aus dem lokalen Cache - kein Netzwerk im Request-Pfad.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional

import requests

from ..data_cache import get_or_fetch, read_cache

SOURCE = "opensanctions_vessels"
MAX_AGE_H = 24.0
CATALOG_URL = "https://data.opensanctions.org/datasets/latest/sanctions/index.json"
HTTP_TIMEOUT = 300
NAME_SIM_THRESHOLD = 0.90

# Lesbare Kurzlabels fuer die Quell-Datasets (sonst der Slug).
_SOURCE_LABELS = {
    "us_ofac_sdn": "OFAC", "us_trade_csl": "US-CSL", "eu_sanctions_map": "EU",
    "eu_journal_sanctions": "EU", "un_1718_vessels": "UN", "gb_fcdo_sanctions": "UK",
    "ch_seco_sanctions": "CH", "ca_dfatd_sema_sanctions": "CA",
    "au_dfat_sanctions": "AU", "fr_tresor_gels_avoir": "FR",
}

_index: Optional[Dict[str, Any]] = None


def normalize_imo(raw: Optional[str]) -> Optional[str]:
    """'IMO9114555' -> '9114555'. None bleibt None."""
    if not raw:
        return None
    return raw.replace("IMO", "").replace("imo", "").strip() or None


def _norm_name(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s.lower())).strip()


def _first(prop: Any) -> Optional[str]:
    if isinstance(prop, list):
        return prop[0] if prop else None
    return prop


# --------------------------------------------------------------------------- #
# Bulk-Download (NUR Background / explizit) - streamt + filtert auf Vessel
# --------------------------------------------------------------------------- #


def _resolve_entities_url() -> str:
    """Loest die aktuelle entities.ftm.json-URL ueber den Katalog auf (Korrektur 1)."""
    idx = requests.get(CATALOG_URL, timeout=60).json()
    for r in idx.get("resources", []):
        if r.get("name") == "entities.ftm.json":
            return r["url"]
    raise RuntimeError("OpenSanctions: entities.ftm.json im Katalog nicht gefunden.")


def fetch_sanctioned_vessels() -> List[Dict[str, Any]]:
    """Streamt die sanctions-Collection und gibt nur die Vessel-Eintraege zurueck."""
    url = _resolve_entities_url()
    out: List[Dict[str, Any]] = []
    import json as _json
    with requests.get(url, stream=True, timeout=HTTP_TIMEOUT) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                e = _json.loads(line)
            except ValueError:
                continue
            if e.get("schema") != "Vessel":
                continue
            p = e.get("properties", {})
            names = p.get("name", []) or []
            aliases = (p.get("previousName", []) or []) + \
                      (p.get("pastNames", []) or []) + \
                      (p.get("alias", []) or []) + names[1:]
            datasets = [d for d in e.get("datasets", []) if d != "sanctions"]
            out.append({
                "id": e.get("id"),
                "name": _first(names),
                "aliases": sorted(set(a for a in aliases if a)),
                "imo": normalize_imo(_first(p.get("imoNumber"))),
                "mmsi": _first(p.get("mmsi")),
                "flag": _first(p.get("flag")),
                "sanctions": datasets,
                "source_url": _first(p.get("sourceUrl")),
            })
    return out


def load_sanctioned_vessels() -> List[Dict[str, Any]]:
    """Gecachte Liste sanktionierter Schiffe (laedt den Bulk, wenn Cache veraltet).

    NICHT im Request-Pfad aufrufen (kann 345 MB ziehen) - fuer refresh/Tests.
    """
    return get_or_fetch(SOURCE, fetch_sanctioned_vessels, MAX_AGE_H)


def refresh() -> Dict[str, Any]:
    """Background-Refresh: Bulk neu laden, cachen, Index verwerfen."""
    data = get_or_fetch(SOURCE, fetch_sanctioned_vessels, MAX_AGE_H, force=True)
    global _index
    _index = None
    n_sources = len({s for v in data for s in v.get("sanctions", [])})
    return {"vessels": len(data), "sources": n_sources}


# --------------------------------------------------------------------------- #
# Index + Matching (Cache-only, request-tauglich)
# --------------------------------------------------------------------------- #


def _build_index() -> Dict[str, Any]:
    """Baut den In-Memory-Index AUS DEM CACHE (kein Download)."""
    data = read_cache(SOURCE) or []
    imo, mmsi, names = {}, {}, {}
    for e in data:
        if e.get("imo"):
            imo[str(e["imo"]).strip()] = e
        if e.get("mmsi"):
            mmsi[str(e["mmsi"]).strip()] = e
        for nm in [e.get("name"), *e.get("aliases", [])]:
            n = _norm_name(nm)
            if n:
                names[n] = e
    return {"imo": imo, "mmsi": mmsi, "names": names, "count": len(data)}


def warmup() -> None:
    """Index einmal aus dem Cache vorladen (App-Start). Kein Netzwerk."""
    global _index
    _index = _build_index()


def _source_label(entry: Dict[str, Any]) -> str:
    for ds in entry.get("sanctions", []):
        if ds in _SOURCE_LABELS:
            return _SOURCE_LABELS[ds]
    s = entry.get("sanctions") or []
    return s[0] if s else "sanctions list"


def match_vessel(mmsi: Optional[str] = None, imo: Optional[str] = None,
                 name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Cache-only Match gegen die Sanktions-Schiffsliste.

    IMO -> MMSI -> Fuzzy-Name (>=0.90). IMO/MMSI = confidence "confirmed",
    Name = "probable". Gibt None ohne Treffer.
    """
    global _index
    if _index is None:
        _index = _build_index()

    imo_n = normalize_imo(imo)
    if imo_n and imo_n in _index["imo"]:
        e = _index["imo"][imo_n]
        return {"confidence": "confirmed", "match": "imo",
                "source": _source_label(e), "name": e.get("name")}
    if mmsi and str(mmsi).strip() in _index["mmsi"]:
        e = _index["mmsi"][str(mmsi).strip()]
        return {"confidence": "confirmed", "match": "mmsi",
                "source": _source_label(e), "name": e.get("name")}

    n = _norm_name(name)
    if n:
        if n in _index["names"]:
            e = _index["names"][n]
            return {"confidence": "probable", "match": "name_exact",
                    "source": _source_label(e), "name": e.get("name")}
        best, best_sim = None, 0.0
        for cand, e in _index["names"].items():
            sim = SequenceMatcher(None, n, cand).ratio()
            if sim >= NAME_SIM_THRESHOLD and sim > best_sim:
                best, best_sim = e, sim
        if best is not None:
            return {"confidence": "probable", "match": "name_fuzzy",
                    "source": _source_label(best), "name": best.get("name"),
                    "similarity": round(best_sim, 2)}
    return None
