"""Mission Radar - Risk Engine.

Das Herzstueck des Systems. Diese Datei ist bewusst frei von jeder Datenquelle:
Die Engine kennt nur die `Vessel`-Datenklasse als Vertrag. In Phase 2 wird die
Datenquelle ausgetauscht (echte AIS-Daten statt synthetischer) - solange diese
Quelle `Vessel`-Objekte liefert, bleibt die Engine unveraendert.

Designgrundsatz: Erklaerbarkeit vor Cleverness. Jede Regel MUSS eine
menschenlesbare Begruendung liefern. Ein Score ohne Begruendung ist hier ein Bug.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

# --------------------------------------------------------------------------- #
# Vertraege (Datenklassen)
# --------------------------------------------------------------------------- #


@dataclass
class Vessel:
    """Ein Schiff zu einem Zeitpunkt - der Input der Engine.

    Dies ist der einzige Vertrag zwischen Datenquelle und Engine. Jede Quelle
    (synthetisch, GFW-API, eigene AIS-Aufzeichnung) muss Objekte dieser Form
    liefern. Felder mit Default sind optional, damit unvollstaendige Quellen
    die Engine nicht sprengen.
    """

    mmsi: str
    name: str
    lat: float
    lon: float
    speed_knots: float = 0.0
    in_protected_area: bool = False
    ais_gap_hours: float = 0.0
    flag: str = "UNK"
    loitering_hours: float = 0.0
    vessel_type: str = "unknown"
    # Optional: erlaubt es, den (cache-only) Sanktions-Check pro Schiff
    # abzuschalten - z. B. im synthetic-Modus. Default True.
    sanctions_check: bool = True

    # Transhipment / Port-Evasion-Felder (alle optional, Default = "unbekannt").
    # -1 / "" = Wert nicht verfuegbar (kein Signal). Nur positive Werte triggern Regeln.
    recent_port_calls: int = -1              # Hafen-Calls letzte 30 Tage; -1 = unbekannt
    days_since_port: float = -1.0           # Tage seit letztem Hafen; -1 = unbekannt
    distance_to_nearest_port_nm: float = -1.0  # NM naechster Hafen; -1 = unbekannt
    nearby_fishing_vessels: int = 0         # Fischer innerhalb 5 nm (letzte 6 h)
    rendezvous_duration_hours: float = 0.0  # h mit anderem Schiff < 0.5 nm bei < 3 kn
    ais_vessel_class: str = ""              # roher AIS Ship Type Code (0-99)


@dataclass
class RiskReason:
    """Eine einzelne Begruendung fuer einen Risikobeitrag.

    points  - numerischer Beitrag zum Score
    label   - kurz, fuer das UI-Badge ("Im Schutzgebiet")
    detail  - ausfuehrlich, fuer den Tooltip / die Erklaerung
    evidence_type - "hard" fuer Treffer auf offiziellen Listen (Fakt einer
                    Behoerde), "heuristic" fuer abgeleitete Verhaltens-Signale.
                    Default "heuristic" (rueckwaertskompatibel).
    """

    points: float
    label: str
    detail: str
    evidence_type: str = "heuristic"


@dataclass
class TargetAssessment:
    """Das Ergebnis der Bewertung eines Schiffs - der Output der Engine."""

    vessel: Vessel
    score: float
    reasons: List[RiskReason] = field(default_factory=list)

    @property
    def top_reason(self) -> Optional[RiskReason]:
        """Die staerkste Einzelbegruendung - fuer eine knappe UI-Zusammenfassung."""
        if not self.reasons:
            return None
        return max(self.reasons, key=lambda r: r.points)


# Eine Regel ist eine Funktion Vessel -> Optional[RiskReason].
Rule = Callable[[Vessel], Optional[RiskReason]]

SCORE_CAP = 100.0


# --------------------------------------------------------------------------- #
# Regeln
# --------------------------------------------------------------------------- #
# Jede Regel ist eine reine Funktion. Gibt sie None zurueck, traegt sie nichts
# zum Score bei. Gibt sie einen RiskReason zurueck, MUSS dieser eine
# menschenlesbare Begruendung enthalten.


def rule_protected_area(v: Vessel) -> Optional[RiskReason]:
    """Aufenthalt in einem ausgewiesenen Schutzgebiet ist das staerkste Signal."""
    if v.in_protected_area:
        return RiskReason(
            points=35,
            label="Im Schutzgebiet",
            detail=(
                "Das Schiff befindet sich innerhalb eines ausgewiesenen "
                "Meeresschutzgebiets (MPA). Fischerei ist hier in der Regel "
                "verboten oder stark eingeschraenkt."
            ),
        )
    return None


def rule_fishing_speed(v: Vessel) -> Optional[RiskReason]:
    """Geschwindigkeiten von 2-5 kn sind typisch fuer aktive Fischerei."""
    if 2.0 <= v.speed_knots <= 5.0:
        return RiskReason(
            # erhoeht 20->25: seltenes Signal (10x in 100 echten Schiffen),
            # daher trennschaerfer (Kalibrierung gg. echte GFW-Verteilung).
            points=25,
            label="Fischerei-Tempo",
            detail=(
                f"Geschwindigkeit {v.speed_knots:.1f} kn liegt im typischen "
                "Bereich aktiver Fischerei (2-5 kn, z. B. Schleppnetz). "
                "Transit laeuft meist deutlich schneller."
            ),
        )
    return None


def rule_ais_gap(v: Vessel) -> Optional[RiskReason]:
    """AIS-Luecken deuten auf bewusstes Abschalten ("going dark") hin.

    Gestufte Bewertung: laengere Luecken sind verdaechtiger. Es wird nur EINE
    Begruendung erzeugt (die hoehere Stufe gewinnt), damit Luecken nicht doppelt
    zaehlen.
    """
    if v.ais_gap_hours >= 12:
        return RiskReason(
            points=25,
            label="AIS-Luecke >=12h",
            detail=(
                f"AIS-Signal {v.ais_gap_hours:.0f}h unterbrochen. Lange Luecken "
                "deuten auf bewusstes Abschalten des Transponders hin "
                '("going dark") - ein klassisches Verschleierungsmuster.'
            ),
        )
    if v.ais_gap_hours >= 4:
        return RiskReason(
            points=10,
            label="AIS-Luecke >=4h",
            detail=(
                f"AIS-Signal {v.ais_gap_hours:.0f}h unterbrochen. Eine moderate "
                "Luecke kann technisch bedingt sein, ist aber beobachtenswert."
            ),
        )
    return None


def rule_loitering(v: Vessel) -> Optional[RiskReason]:
    """Langes Verweilen auf engem Raum deutet auf Fang/Umladen statt Transit."""
    # Schwelle 6h->12h gesenkt UND Gewicht 15->10: feuerte bei ~50% echter
    # Schiffe -> zu wenig Diskriminierung (Grundrauschen). Strengere Schwelle +
    # niedrigeres Gewicht (Kalibrierung gg. echte GFW-Verteilung).
    if v.loitering_hours >= 12:
        return RiskReason(
            points=10,
            label="Verweilen >=12h",
            detail=(
                f"Das Schiff verweilt seit {v.loitering_hours:.0f}h auf engem "
                "Raum. Anhaltendes Loitering deutet auf Fang oder Umladung "
                "(Transshipment) hin, nicht auf Durchfahrt."
            ),
        )
    return None


# Flags of Convenience: in IUU-Listen ueberproportional vertreten.
# Quelle: FAO IUU Vessel List; Trygg Mat Tracking (TMT).
# Beide ISO-Schreibweisen, weil GFW 3-stellige Codes (NGA/PAN) liefert, die
# Anker/Listen teils 2-stellige (NG/PA) - sonst wuerde die Regel auf echten
# Daten nie feuern.
FLAGS_OF_CONVENIENCE = {
    "NGA", "NG",   # Nigeria
    "GNQ", "GQ",   # Aequatorialguinea
    "TGO", "TG",   # Togo
    "BLZ", "BZ",   # Belize
    "PAN", "PA",   # Panama
    "COM", "KM",   # Komoren
    "STP", "ST",   # Sao Tome und Principe
}


def rule_flag_of_convenience(v: Vessel) -> Optional[RiskReason]:
    """Bekannte Billigflaggen, die in IUU-Faellen ueberproportional auftauchen."""
    # neue Regel (+20): Flags of Convenience korrelieren mit IUU-Fischerei
    # (FAO IUU Vessel List, Trygg Mat Tracking). Kalibrierung: ergaenzt ein
    # identitaetsbasiertes Signal, das Speed/Gap-Regeln nicht abdecken.
    if v.flag and v.flag.upper() in FLAGS_OF_CONVENIENCE:
        return RiskReason(
            points=20,
            label="Billigflagge",
            detail=(
                f"Flagge {v.flag} zaehlt zu den 'Flags of Convenience', die in "
                "IUU-Listen (FAO, Trygg Mat Tracking) stark ueberrepraesentiert "
                "sind - ein identitaetsbasiertes Risikosignal."
            ),
        )
    return None


# --------------------------------------------------------------------------- #
# Regeln aus STATISCHEN, gecachten offiziellen Quellen (NUR Cache-Lookups).
# Diese Regeln machen KEINEN Netzwerk-Call - sie lesen aus dem lokalen Cache
# (app/sources/*, gefuellt per Hintergrund-Job). Treffer auf offiziellen Listen
# sind "hard evidence" (Fakt einer Behoerde), kein abgeleiteter Verdacht.
# --------------------------------------------------------------------------- #


def rule_iuu_list_hit(v: Vessel) -> Optional[RiskReason]:
    """Schiff steht auf einer offiziellen IUU-Liste (CCAMLR/RFMO/TMT)."""
    from .sources import iuu_list
    hit = iuu_list.lookup(getattr(v, "mmsi", None), getattr(v, "imo", None), v.name)
    if hit:
        return RiskReason(
            points=50, evidence_type="hard", label="Auf IUU-Liste",
            detail=("Treffer auf einer offiziellen IUU-Schiffsliste "
                    f"(Match ueber {hit['match']}). Autoritative Quelle - "
                    "kein abgeleiteter Verdacht."),
        )
    return None


def rule_sanctions_hit(v: Vessel) -> Optional[RiskReason]:
    """Schiff steht auf einer offiziellen Sanktionsliste (OpenSanctions, cache-only).

    Eigene Risiko-Dimension (sanktionierte Tanker: Russland/Iran/Nordkorea) -
    NICHT identisch mit IUU-Fischerei. Ergaenzt das IUU-Signal, doppelt es nicht.
    """
    if not getattr(v, "sanctions_check", True):
        return None
    from .sources import opensanctions
    hit = opensanctions.match_vessel(
        mmsi=getattr(v, "mmsi", None), imo=getattr(v, "imo", None), name=v.name)
    if not hit:
        return None
    if hit["confidence"] == "confirmed":
        return RiskReason(
            points=40, evidence_type="hard", label="SANCTIONS HIT",
            detail=(f"Vessel on {hit['source']} sanctions list - confirmed "
                    f"{hit['match'].upper()} match (OpenSanctions)."),
        )
    return RiskReason(   # probable (Name-Match) -> behavioral, manuell pruefen
        points=25, evidence_type="behavioral", label="Probable Sanctions Match",
        detail=(f"Name match against {hit['source']} sanctions list "
                "(OpenSanctions) - verify manually."),
    )


def rule_port_detention(v: Vessel) -> Optional[RiskReason]:
    """Schiff hat eine Detention-Historie (Paris/Tokyo MOU)."""
    from .sources import port_control
    hit = port_control.lookup(getattr(v, "imo", None))
    if hit:
        return RiskReason(
            points=15, evidence_type="hard", label="Hafen-Detention",
            detail=(f"{hit['detentions']} dokumentierte Festhaltung(en) durch Port "
                    "State Control (Paris/Tokyo MOU)."),
        )
    return None


def rule_eez_violation(v: Vessel) -> Optional[RiskReason]:
    """Schiff in fremder EEZ (Flagge != Kuestenstaat) - ohne Lizenz-Kontext."""
    from .sources import eez
    zone = eez.eez_at(v.lat, v.lon)
    if zone and zone != "?" and (v.flag or "").upper()[:3] != zone.upper()[:3]:
        return RiskReason(
            points=10, label="Fremde EEZ",
            detail=(f"Position in der EEZ von {zone}, Schiffsflagge {v.flag}. "
                    "Ohne Lizenznachweis beobachtenswert (Lizenz-Kontext fehlt)."),
        )
    return None


# Die Regel-Registry. NEUE REGELN werden ausschliesslich hier eingetragen -
# kein anderer Code muss angefasst werden.
RULES: List[Rule] = [
    rule_protected_area,
    rule_fishing_speed,
    rule_ais_gap,
    rule_loitering,
    rule_flag_of_convenience,
    # statische, gecachte offizielle Quellen (Cache-only):
    rule_iuu_list_hit,
    rule_sanctions_hit,
    rule_port_detention,
    rule_eez_violation,
]

# Transhipment-Signale (separates Modul - gleicher Vertrag wie RULES).
# Lazy import: vermeidet zirkulaere Imports (transhipment_engine importiert Vessel).
def _load_transhipment_rules() -> "List[Rule]":
    from .transhipment_engine import (
        signal_remote_reefer,
        signal_rendezvous,
        signal_mpa_reefer,
        signal_port_evasion,
        signal_dark_fleet_proximity,
    )
    return [
        signal_remote_reefer,
        signal_rendezvous,
        signal_mpa_reefer,
        signal_port_evasion,
        signal_dark_fleet_proximity,
    ]


TRANSHIPMENT_RULES: List[Rule] = _load_transhipment_rules()


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


def assess(vessel: Vessel, rules: Optional[List[Rule]] = None) -> TargetAssessment:
    """Bewertet ein einzelnes Schiff gegen alle Regeln.

    Score = Summe aller Begruendungen, gedeckelt bei SCORE_CAP (100).
    """
    active_rules = rules if rules is not None else RULES
    reasons: List[RiskReason] = []
    for rule in active_rules:
        reason = rule(vessel)
        if reason is not None:
            reasons.append(reason)

    raw_score = sum(r.points for r in reasons)
    score = min(raw_score, SCORE_CAP)
    # Staerkste Begruendung zuerst - die UI zeigt sie als top_reason.
    reasons.sort(key=lambda r: r.points, reverse=True)
    return TargetAssessment(vessel=vessel, score=score, reasons=reasons)


def assess_all(vessels: List[Vessel]) -> List[TargetAssessment]:
    """Bewertet alle Schiffe (z. B. fuer die Kartendarstellung)."""
    return [assess(v) for v in vessels]


def compound_score(vessel: Vessel) -> TargetAssessment:
    """Volle Bewertung: alle Regeln + Transhipment-Signale + Compound-Multiplier.

    Aequivalent zu assess(), aber mit RULES + TRANSHIPMENT_RULES. Wenn Remote-Reefer-
    und Rendezvous-Signal gleichzeitig feuern, wird der Roh-Score mit 1.4 multipliziert
    (Synergieeffekt: starke Konfidenz bei Kombination). Cap bleibt bei 100.

    Backward-kompatibel: assess() bleibt unveraendert fuer alle bestehenden Aufrufe.
    """
    from .transhipment_engine import (
        COMPOUND_MULTIPLIER, COMPOUND_TRIGGER_LABELS, compound_explanation,
    )
    all_rules = RULES + TRANSHIPMENT_RULES
    reasons: List[RiskReason] = []
    for rule in all_rules:
        reason = rule(vessel)
        if reason is not None:
            reasons.append(reason)

    raw_score = sum(r.points for r in reasons)

    fired_labels = {r.label for r in reasons}
    if COMPOUND_TRIGGER_LABELS.issubset(fired_labels):
        reasons.append(compound_explanation(raw_score))
        raw_score *= COMPOUND_MULTIPLIER

    score = min(raw_score, SCORE_CAP)
    reasons.sort(key=lambda r: r.points, reverse=True)
    return TargetAssessment(vessel=vessel, score=score, reasons=reasons)


def rank_targets(vessels: List[Vessel], top_n: int = 5) -> List[TargetAssessment]:
    """Liefert die Top-N Ziele nach Score absteigend.

    Ziele mit Score 0 (keine einzige Begruendung) werden ausgeschlossen - ein
    Ziel ohne Begruendung ist im Sinne dieses Projekts kein Ziel.
    """
    assessments = [a for a in assess_all(vessels) if a.score > 0]
    assessments.sort(key=lambda a: a.score, reverse=True)
    return assessments[:top_n]
