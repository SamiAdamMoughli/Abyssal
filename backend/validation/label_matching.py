"""Matching geladener Schiffe gegen die offizielle IUU-Liste.

Bestimmt, welche Schiffe aus der Pipeline (synthetisch, GFW oder die
behavioralen known_cases-Fixtures) mit einem offiziellen IUU-Eintrag
uebereinstimmen. Reines Lese-/Vergleichs-Tool - keine Engine-Aenderung.

Matching-Staerke (absteigend):
  1. IMO exakt          - staerkster Match (eindeutige Schiffskennung)
  2. MMSI exakt         - stark (aber CCAMLR publiziert keine MMSI -> selten)
  3. Name exakt         - normalisiert gleich zu Name ODER einem Alias
  4. Name probable      - Fuzzy-Aehnlichkeit >= 0.90 (difflib, stdlib)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, List, Optional

from .iuu_official_list import IUUVessel, load_iuu_vessels

NAME_SIM_THRESHOLD = 0.90


@dataclass
class IUUMatch:
    """Ein Treffer: ein Pipeline-Schiff <-> ein offizieller IUU-Eintrag."""

    vessel: Any                 # das bewertete Schiff (Vessel)
    iuu: IUUVessel
    match_type: str             # "imo" | "mmsi" | "name_exact" | "name_probable"
    similarity: float           # 1.0 fuer exakte, sonst difflib-ratio
    matched_on: str             # konkreter Name/Wert, der gematcht hat


def _norm(s: Optional[str]) -> str:
    """Normalisiert einen Namen fuer den Vergleich (klein, ohne Sonderzeichen)."""
    if not s:
        return ""
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", " ", s)   # Punkte/Bindestriche etc. raus
    return re.sub(r"\s+", " ", s).strip()


def _match_one(vessel: Any, iuu_list: List[IUUVessel]) -> Optional[IUUMatch]:
    """Sucht den besten Match fuer EIN Schiff (oder None)."""
    v_imo = getattr(vessel, "imo", None)     # Engine-Vessel hat (noch) kein imo
    v_mmsi = getattr(vessel, "mmsi", None)
    v_name = _norm(getattr(vessel, "name", None))

    best: Optional[IUUMatch] = None
    for iuu in iuu_list:
        # 1) IMO exakt
        if v_imo and iuu.imo and str(v_imo).strip() == str(iuu.imo).strip():
            return IUUMatch(vessel, iuu, "imo", 1.0, str(iuu.imo))
        # 2) MMSI exakt
        if v_mmsi and iuu.mmsi and str(v_mmsi).strip() == str(iuu.mmsi).strip():
            return IUUMatch(vessel, iuu, "mmsi", 1.0, str(iuu.mmsi))
        # 3/4) Name exakt oder fuzzy gegen Name + alle Aliasse
        if v_name:
            v_tokens = set(v_name.split())
            for cand in iuu.all_names():
                nc = _norm(cand)
                if not nc:
                    continue
                if nc == v_name:
                    return IUUMatch(vessel, iuu, "name_exact", 1.0, cand)
                # Token-Subset: alle Tokens des einen Namens im anderen enthalten
                # (faengt "STS-50 (Andrey Dolgov)" <-> "STS-50" o. ae.). Min. 1
                # mehrstelliges Token, um Zufallstreffer zu vermeiden.
                c_tokens = set(nc.split())
                if c_tokens and (c_tokens <= v_tokens or v_tokens <= c_tokens) \
                        and max(len(t) for t in (c_tokens & v_tokens) or {""}) >= 3:
                    sim = len(c_tokens & v_tokens) / max(len(c_tokens | v_tokens), 1)
                    if best is None or sim > best.similarity:
                        best = IUUMatch(vessel, iuu, "name_probable", sim, cand)
                    continue
                sim = SequenceMatcher(None, v_name, nc).ratio()
                if sim >= NAME_SIM_THRESHOLD and (best is None or sim > best.similarity):
                    best = IUUMatch(vessel, iuu, "name_probable", sim, cand)
    return best


def match_against_iuu(vessels: List[Any],
                      iuu_list: Optional[List[IUUVessel]] = None) -> List[IUUMatch]:
    """Matcht eine Liste geladener Schiffe gegen die offizielle IUU-Liste.

    Gibt fuer jedes Schiff mit Treffer einen IUUMatch zurueck (max. einen,
    den staerksten). Schiffe ohne Treffer erscheinen nicht im Ergebnis.
    """
    iuu_list = iuu_list if iuu_list is not None else load_iuu_vessels()
    matches: List[IUUMatch] = []
    for v in vessels:
        m = _match_one(v, iuu_list)
        if m is not None:
            matches.append(m)
    return matches
