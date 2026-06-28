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

    # Motion profile (computed from vessel_tracks sliding window).
    # "unknown" when fewer than 3 pings are available in the DB.
    behavior: str = "unknown"              # transit/trawling/loitering/anchored
    behavior_confidence: float = 0.0      # 0.0–1.0
    cog_degrees: float = -1.0             # current course over ground

    # Spatial features (computed from vessel_tracks + zone geometry).
    # -1.0 = no zone geometry loaded / unknown.
    nearest_mpa_nm: float = -1.0         # nautical miles to nearest MPA boundary
    time_in_zone_hours: float = 0.0      # hours in current zone (from track history)
    border_skirting: bool = False        # sustained near-boundary without entering

    # Trajectory pattern (geometric shape of the 6-24 h route).
    trajectory_pattern: str = "unknown"  # grid/holding/spiral/transit/anomaly
    trajectory_confidence: float = 0.0

    # Vessel-to-vessel interaction (populated by proximity detection).
    # "" = no active encounter detected this cycle.
    rendezvous_partner_type: str = ""  # vessel type of the proximate partner
    rendezvous_meeting_class: str = ""  # classified encounter type

    # AIS gap kinematic analysis + spoofing signals.
    # gap_type: tactical_dark / technical_failure / spoofing / "" (unknown)
    gap_type: str = ""
    gap_displacement_nm: float = -1.0
    spoofing_flag: bool = False          # kinematic violation detected
    spoofing_max_speed_kn: float = 0.0  # highest implied speed in track

    # Contextual fusion (environmental raster + registry cache).
    # Sentinels: sst_celsius=-999 / wave_height_m=-1 / wind_speed_kn=-1 = no data.
    sst_celsius: float = -999.0          # Sea Surface Temperature at position
    wave_height_m: float = -1.0          # significant wave height (CMEMS)
    wind_speed_kn: float = -1.0          # 10-m wind speed
    sst_at_thermal_front: bool = False   # SST gradient ≥ 2°C detected nearby
    historical_risk_score: float = -1.0  # highest score in last 30 days; -1 = new
    verified_vessel_type: str = ""       # registry type (IHS/Equasis); "" = unknown

    # Biodiversity context from cached OBIS occurrences near the vessel.
    bio_risk: str = "unknown"            # high/medium/low/none/unknown
    bio_species_count: int = 0
    bio_threatened_species: List[str] = field(default_factory=list)
    bio_cetaceans: List[str] = field(default_factory=list)
    bio_sea_turtles: List[str] = field(default_factory=list)
    bio_sharks_rays: List[str] = field(default_factory=list)

    # Non-collaborative remote sensing: cached SAR/VIIRS/optical detections
    # nearby with no matching AIS ping in the same H3 cell/time window.
    dark_detection_count: int = 0
    dark_detection_sources: List[str] = field(default_factory=list)
    nearest_dark_detection_nm: float = -1.0


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
    """AIS-Luecken bewertet mit kinematischer Plausibilitaet.

    Wenn gap_type bekannt ist (kinematische Analyse abgeschlossen), wird der
    Score durch die Reiseart beim Wiederauftauchen verfeinert:
      TACTICAL_DARK   — Schiff hat sich kaum bewegt: stundenlange Stille,
                        minimale Positionsveraenderung → bewusstes Verstecken.
      SPOOFING        — Positionssprung waehrend Gap ist physikalisch unmoeglich.
      TECHNICAL_FAILURE — Schiff setzte Kurs plausibel fort; technischer Ausfall.
      (leer)          — Keine Wiederauftauch-Daten: generische Stufenbewertung.
    """
    gt = (v.gap_type or "").lower()

    if gt == "spoofing":
        return RiskReason(
            points=45,
            label="Positions-Sprung (Spoofing)",
            detail=(
                f"Positions-Sprung waehrend {v.ais_gap_hours:.0f}h AIS-Luecke: "
                f"erforderliche Geschwindigkeit > 50 kn ({v.gap_displacement_nm:.0f} nm). "
                "Physikalisch unmoeglich fuer Oberflaechenschiff. "
                "Koordinaten-Spoofing hochwahrscheinlich."
            ),
            evidence_type="hard",
        )

    if gt == "tactical_dark":
        return RiskReason(
            points=40,
            label="Taktisches Abschalten",
            detail=(
                f"{v.ais_gap_hours:.0f}h AIS-Stille, danach nur "
                f"{v.gap_displacement_nm:.0f} nm Positionswechsel. "
                "Schiff haette in dieser Zeit weit reisen koennen — "
                "blieb aber lokal. Klassisches 'Going Dark' zum Verstecken. "
                "(Millefiori et al. 2021)"
            ),
        )

    if gt == "technical_failure":
        if v.ais_gap_hours >= 12:
            return RiskReason(
                points=15,
                label="AIS-Ausfall >=12h (technisch)",
                detail=(
                    f"AIS-Signal {v.ais_gap_hours:.0f}h unterbrochen. "
                    "Kinematische Analyse deutet auf technischen Ausfall hin "
                    "(Positionswechsel plausibel zur deklarierten Geschwindigkeit). "
                    "Verlaengerte technische Ausfaelle sind dennoch beobachtenswert."
                ),
            )
        return None

    # Fallback: gap_type unknown (no reappearance data yet)
    if v.ais_gap_hours >= 12:
        return RiskReason(
            points=25,
            label="AIS-Luecke >=12h",
            detail=(
                f"AIS-Signal {v.ais_gap_hours:.0f}h unterbrochen. Lange Luecken "
                "deuten auf bewusstes Abschalten des Transponders hin "
                '("going dark"). Kinematische Analyse folgt bei Wiederauftauchen.'
            ),
        )
    if v.ais_gap_hours >= 4:
        return RiskReason(
            points=10,
            label="AIS-Luecke >=4h",
            detail=(
                f"AIS-Signal {v.ais_gap_hours:.0f}h unterbrochen. "
                "Moderate Luecke — beobachtenswert, koennte technisch bedingt sein."
            ),
        )
    return None


def rule_kinematic_violation(v: Vessel) -> Optional[RiskReason]:
    """Physikalisch unmoeglich schneller Positions-Sprung zwischen AIS-Pings.

    Wenn die implizierte Geschwindigkeit zwischen zwei aufeinanderfolgenden
    Pings die physikalische Grenze (50 kn) uebersteigt, ist mindestens eine
    der Koordinaten gefaelscht — haertestes Spoofing-Indiz aus reine AIS-Daten.
    """
    if not v.spoofing_flag:
        return None
    if v.spoofing_max_speed_kn < 50.0:
        return None
    return RiskReason(
        points=40,
        label="Kinematik-Verletzung (Spoofing)",
        detail=(
            f"Implizierte Geschwindigkeit zwischen zwei AIS-Pings: "
            f"{v.spoofing_max_speed_kn:.0f} kn. "
            "Kein Oberflaechenschiff kann mehr als ~50 kn fahren. "
            "Koordinaten-Spoofing durch skriptgenerierte Position nachgewiesen. "
            "(Vespe et al. 2016)"
        ),
        evidence_type="hard",
    )


def rule_static_coords(v: Vessel) -> Optional[RiskReason]:
    """Gemeldete SOG > 2 kn, aber Koordinaten aendern sich kaum.

    Echte GPS-Empfaenger auf schwankenden Schiffen haben immer natuerliches
    Rauschen. Wenn ein Schiff Fahrt meldet, aber die Position eingefroren ist,
    wurden die Koordinaten synthetisch generiert.
    """
    if not v.spoofing_flag:
        return None
    if v.spoofing_max_speed_kn >= 50.0:
        return None  # kinematic violation already explains spoofing
    return RiskReason(
        points=20,
        label="Statische Koordinaten (Spoofing)",
        detail=(
            "Schiff meldet Fahrt > 2 kn, aber GPS-Koordinaten aendern sich "
            "kaum zwischen Pings. Echte Koordinaten haben immer natuerliches "
            "Rauschen. Verdacht auf skriptgenerierte Fake-Position."
        ),
        evidence_type="heuristic",
    )


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


def rule_mpa_proximity(v: Vessel) -> Optional[RiskReason]:
    """Vessel within 5 nm of an MPA boundary — pre-alert buffer zone.

    Fires only when the vessel is outside the zone (inside is caught by
    rule_protected_area). Distance -1 means unknown → rule stays silent.
    Tiered: closer = more points.
    """
    nm = v.nearest_mpa_nm
    if nm < 0:
        return None
    if nm == 0.0 or v.in_protected_area:
        return None  # inside zone — rule_protected_area already fires
    if nm <= 2.0:
        return RiskReason(
            points=12,
            label="Pufferzone <2 nm",
            detail=(
                f"{nm:.1f} nm vor der MPA-Grenze. Unmittelbare Naeherung "
                "an das Schutzgebiet — Grenzuebertritt moeglich."
            ),
        )
    if nm <= 5.0:
        return RiskReason(
            points=6,
            label="Pufferzone <5 nm",
            detail=(
                f"{nm:.1f} nm vor der MPA-Grenze. Schiff befindet sich "
                "in der 5-sm-Pufferzone (Pre-Alert)."
            ),
        )
    return None


def rule_time_in_mpa(v: Vessel) -> Optional[RiskReason]:
    """Sustained presence inside an MPA from track history.

    rule_protected_area fires at entry; this rule adds weight after the
    vessel has been inside long enough to signal active (not accidental) fishing.
    Tiered by duration.
    """
    if not v.in_protected_area or v.time_in_zone_hours <= 0:
        return None
    h = v.time_in_zone_hours
    if h >= 6.0:
        return RiskReason(
            points=20,
            label=f"In MPA seit >{int(h)}h",
            detail=(
                f"Schiff ist seit {h:.1f} Stunden ununterbrochen im Schutzgebiet. "
                "Anhaltende Praesenz deutet auf aktive Fischerei hin, nicht auf Durchfahrt."
            ),
        )
    if h >= 2.0:
        return RiskReason(
            points=12,
            label=f"In MPA seit >{int(h)}h",
            detail=(
                f"Schiff ist seit {h:.1f} Stunden im Schutzgebiet "
                "(Mindest-Schwelle fuer aktive Praesenz ueberschritten)."
            ),
        )
    return None


def rule_border_skirting(v: Vessel) -> Optional[RiskReason]:
    """Vessel repeatedly hugs the MPA boundary without crossing — spillover fishing.

    Fishing vessels exploit the 'spillover effect': fish that migrate out of
    the protected zone are harvested just outside the boundary. The vessel's
    track stays consistently within a few nautical miles without ever entering.
    """
    if v.border_skirting:
        nm = v.nearest_mpa_nm
        dist_str = f"{nm:.1f} nm" if nm >= 0 else "nahe der Grenze"
        return RiskReason(
            points=18,
            label="Grenz-Schleichen",
            detail=(
                f"Schiff bewegt sich seit mehreren Stunden entlang der MPA-Grenze "
                f"(akt. Abstand: {dist_str}) ohne einzutreten. "
                "Typisches Muster fuer Spillover-Fischerei."
            ),
        )
    return None


def rule_pair_transshipment(v: Vessel) -> Optional[RiskReason]:
    """Fishing vessel with an active reefer/carrier as encounter partner.

    `signal_rendezvous` in transhipment_engine fires on the REEFER side.
    This rule fires on the FISHING VESSEL side — it sees the reefer as
    its proximate partner, making the pair mutually incriminating.
    Only fires when our own proximity engine detected the pair; it does
    NOT fire from GFW encounter data alone (GFW populates nearby_fishing_vessels
    on the reefer, not rendezvous_partner_type on the fisher).
    """
    _FISHING = {
        "fishing", "trawler", "longliner",
        "purse_seiner", "squid_jigger",
    }
    _REEFER = {"reefer", "carrier", "refrigerated", "fish_carrier"}

    if (v.vessel_type or "").lower() not in _FISHING:
        return None
    pt = (v.rendezvous_partner_type or "").lower()
    if not pt or pt not in _REEFER:
        return None
    dur = v.rendezvous_duration_hours
    if dur < 0.5:
        return None
    return RiskReason(
        points=25,
        label="Transshipment-Rendezvous",
        detail=(
            f"Fischereifahrzeug seit {dur:.1f} h in Nahbereich eines "
            f"Kuehlschiffs (Typ: {pt}). Direkter Beleg fuer potentiellen "
            "Fanguebergabe auf See. Kombination Fischfangschiff + Reefer "
            "ist staerkster Transhipment-Indikator aus AIS-Daten. "
            "(Kroodsma et al. 2018, GFW)"
        ),
        evidence_type="hard",
    )


def rule_trajectory_pattern(v: Vessel) -> Optional[RiskReason]:
    """Geometric fingerprint of the 6-24 h route shape.

    Complements motion-profile (instantaneous kinematics) with long-range
    shape evidence. ANOMALY fires when a non-fishing vessel traces a fishing
    geometry — the most powerful signal for dark fleet behaviour.
    """
    tp = v.trajectory_pattern
    tc = v.trajectory_confidence
    if tp == "unknown" or tc < 0.5:
        return None

    FISHING_TYPES = {"trawler", "longliner", "purse_seiner", "fishing"}
    vt = v.vessel_type or "unknown"

    if tp == "anomaly":
        return RiskReason(
            points=20,
            label="Trajektorie-Anomalie",
            detail=(
                f"Schiff vom Typ '{vt}' faehrt ein Fischerei-Zickzack-Muster "
                f"(Konfidenz {tc:.0%}). Nicht-Fischfahrzeuge haben keinen "
                "legitimen Grund fuer diese Trajektorie-Form."
            ),
        )
    if tp == "grid":
        if vt in FISHING_TYPES:
            return RiskReason(
                points=10,
                label="Gitter-Trajektorie",
                detail=(
                    f"Schlepp-Fischereibewegung geometrisch bestaetigt: "
                    f"parallele Schleifen mit ~90°-Kehren (Konfidenz {tc:.0%}). "
                    "Erhaertet den Trawling-Verdacht."
                ),
            )
        return RiskReason(
            points=15,
            label="Unerwartetes Gitter-Muster",
            detail=(
                f"Schiff vom Typ '{vt}' faehrt Fischereigeometrie (Konfidenz "
                f"{tc:.0%}). Typ-Muster-Abweichung ist verdaechtig."
            ),
        )
    if tp == "holding":
        return RiskReason(
            points=12,
            label="Warte-Trajektorie",
            detail=(
                f"Schiff fährt geschlossene Schleifen auf offener See "
                f"(Konfidenz {tc:.0%}). Typisch fuer heimliche Rendezvous-Warten "
                "oder ungemeldetes Schiff-zu-Schiff-Transfer."
            ),
        )
    if tp == "spiral":
        return RiskReason(
            points=6,
            label="Spiral-Muster",
            detail=(
                f"Spiralfoermige Trajektorie (Konfidenz {tc:.0%}). "
                "Moeglicherweise SAR, Kalibrierung — oder Netze einholend."
            ),
        )
    return None


def rule_behavior_profile(v: Vessel) -> Optional[RiskReason]:
    """Kinematic pattern from motion profile analysis (track-history-based).

    Fires only when a motion profile has been computed (>= 3 historical pings).
    TRAWLING adds points as kinematic confirmation on top of the speed signal.
    LOITERING from raw pings is orthogonal to GFW's pre-computed loitering_hours
    and adds independent evidence of suspicious lingering.
    """
    if v.behavior == "trawling" and v.behavior_confidence >= 0.5:
        return RiskReason(
            points=12,
            label="Trawling-Muster",
            detail=(
                f"Bewegungsprofil bestaetigt Schlepp-Fischereibewegung: "
                f"Tortuositaet hoch, Tempo 2–5 kn, rhythmische Kurswechsel "
                f"(Konfidenz {v.behavior_confidence:.0%})."
            ),
        )
    if v.behavior == "loitering" and v.behavior_confidence >= 0.5:
        return RiskReason(
            points=8,
            label="Loitering-Muster",
            detail=(
                f"Bewegungsprofil: Schiff kreist auf engem Raum ohne klares "
                f"Fahrziel (SOG < 3 kn, hohe Tortuositaet, chaotische Kurse). "
                f"Konfidenz {v.behavior_confidence:.0%}."
            ),
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


def rule_thermal_front_loitering(v: Vessel) -> Optional[RiskReason]:
    """Fischerei-Verdacht erhoehen, wenn Schiff an einer Temperaturfront lauert.

    Thunfisch (Gelbflossenthun, Weissem Thun) aggregiert bevorzugt an SST-Fronten
    — Grenzen zwischen warmem und kaltem Wasser (15-28°C-Bereich). Ein Fischerboot,
    das dort langsam faehrt oder lauert, ist sehr wahrscheinlich aktiv beim Fischen.
    Schiffe, die nicht offiziell als Fischer gemeldet sind, aber dieses Muster zeigen,
    erhalten ebenfalls erhoehte Punkte (undeklarierer Fischereiverdacht).

    Quellen: Zainuddin et al. (2017); Lehodey et al. (2008) SEAPODYM-Modell.
    """
    from spyhop.analytics.context_fusion import (  # noqa: PLC0415
        in_tuna_thermal_range, SST_NO_DATA,
    )
    sst = v.sst_celsius
    if sst == SST_NO_DATA:
        return None
    if not (v.sst_at_thermal_front or in_tuna_thermal_range(sst)):
        return None

    loitering = v.loitering_hours > 0.5 or v.behavior in ("loitering", "trawling")
    if not loitering:
        return None

    vtype = (v.vessel_type or "").lower()
    is_fishing = any(
        kw in vtype for kw in (
            "fishing", "trawler", "seiner", "longliner", "pole", "dredger"
        )
    )
    front_str = "Temperaturfront" if v.sst_at_thermal_front else f"{sst:.1f}°C (Thunfisch-Zone)"

    if is_fishing:
        return RiskReason(
            points=20,
            label="Fischerei an Temperaturfront",
            detail=(
                f"Fischerboot an {front_str}: Thunfischarten sammeln sich an "
                "SST-Fronten (Grenze zwischen warmem/kaltem Wasser). "
                f"Schiff lauert seit {v.loitering_hours:.1f} h. "
                "Aktive Fischerei wahrscheinlich. (Zainuddin et al. 2017)"
            ),
        )
    return RiskReason(
        points=12,
        label="Undeklariertes Fischen vermutet",
        detail=(
            f"Nicht-Fischer an {front_str} — lauert wie ein Fischerboot. "
            "Moegliche Typverschleierung oder undeklariete Fischerei."
        ),
    )


def rule_weather_suppression(v: Vessel) -> Optional[RiskReason]:
    """Sturm-Entlastung: Driften bei schwerem Wetter ist kein Verhaltenssignal.

    Wenn ein Schiff bei Wellenhöhen >= 5 m oder Windstaerken >= 40 kn nur
    langsam faehrt oder driftet ('beigedreht'), ist das eine normale nautische
    Sicherheitsmassnahme ('Abwettern'). Ohne Wetterdaten wuerde das System
    faelschlicherweise ein Rendezvous oder ein Loitering-Signal ausloesen.

    Gibt einen negativen Risiko-Kredit zurueck, der das automatisch generierte
    Loitering-Signal (-rule_loitering) und aehnliche Signale abmildert.
    """
    from spyhop.analytics.context_fusion import is_storm_conditions  # noqa: PLC0415
    if not is_storm_conditions(v.wave_height_m, v.wind_speed_kn):
        return None
    if v.speed_knots > 3.0:
        return None

    wave_str = (
        f"Wellenhoehe {v.wave_height_m:.1f} m"
        if v.wave_height_m >= 0 else ""
    )
    wind_str = (
        f"Wind {v.wind_speed_kn:.0f} kn"
        if v.wind_speed_kn >= 0 else ""
    )
    cond = " / ".join(filter(None, [wave_str, wind_str]))

    return RiskReason(
        points=-20,
        label="Sturm-Entlastung",
        detail=(
            f"Schiff driftet bei extremem Wetter ({cond}). "
            "Normales Abwettern nach WMO-Skala 7-8 — kein Verhaltens-Alarm. "
            "Loitering-Signal wird reduziert. (CMEMS Wellenhoehenprodukt)"
        ),
        evidence_type="heuristic",
    )


def rule_historical_offender(v: Vessel) -> Optional[RiskReason]:
    """Wiederholungstaeter: Schiff hat in der Vergangenheit hohen Risikowert.

    Ein hohes historisches Profil erhoeh die Prior-Wahrscheinlichkeit aktueller
    illegaler Aktivitaet. Schiffe, die wiederholt in Schutzgebieten, auf
    Blacklists oder mit AIS-Luecken aufgefallen sind, sollen nicht 'nochmal
    vom Null' starten koennen.
    """
    hs = v.historical_risk_score
    if hs < 0:
        return None  # new vessel, no history

    if hs >= 80:
        return RiskReason(
            points=20,
            label="Chronischer Risikotraeger",
            detail=(
                f"Historischer Risikowert: {hs:.0f}/100 in den letzten 30 Tagen. "
                "Schiff ist wiederholt durch Hochrisiko-Verhalten aufgefallen. "
                "Erhoehte Prior-Wahrscheinlichkeit illegaler Aktivitaet."
            ),
        )
    if hs >= 60:
        return RiskReason(
            points=12,
            label="Vorangehende Risiko-Vorfaelle",
            detail=(
                f"Historischer Risikowert: {hs:.0f}/100. "
                "Schiff hat frueher Risiko-Signale ausgeloest — verstaerkte Beobachtung."
            ),
        )
    return None


def rule_type_mismatch(v: Vessel) -> Optional[RiskReason]:
    """AIS-Typ widerspricht dem offiziellen Registertyp — Identitaetsverschleierung.

    Betreiber illegaler Fischerei tragen gelegentlich einen anderen Schiffstyp in
    das AIS ein, um Fischerei-Kontrollsysteme zu umgehen. Ein im Register als
    'Trawler' eingetragenes Schiff, das per AIS als 'Cargo' sendet, ist ein
    starkes Indiz fuer eine bewusste Manipulation.

    Datenquelle: MMSI-Profil-Cache (IHS Markit / Equasis), 30-Tage-Redis-Cache.
    """
    from spyhop.analytics.context_fusion import type_mismatch_severity  # noqa: PLC0415
    severity = type_mismatch_severity(
        v.vessel_type or "", v.verified_vessel_type or ""
    )
    if severity is None:
        return None

    if severity == "critical":
        return RiskReason(
            points=30,
            label="AIS-Typ gefaelscht (Fischerei↔Cargo)",
            detail=(
                f"Register (IHS/Equasis): '{v.verified_vessel_type}' — "
                f"AIS meldet: '{v.vessel_type}'. "
                "Ein als Fischerboot registriertes Schiff sendet als Frachter: "
                "klassische Fischerei-Tarnung. (IMO Circular FAL.2/Circ.127)"
            ),
            evidence_type="hard",
        )
    return RiskReason(
        points=10,
        label="AIS-Typ abweichend vom Register",
        detail=(
            f"Register: '{v.verified_vessel_type}' — AIS: '{v.vessel_type}'. "
            "Typ-Diskrepanz beobachtenswert. Koennte auf manuelle Aenderung hindeuten."
        ),
    )


def rule_bio_risk_fishing(v: Vessel) -> Optional[RiskReason]:
    """Slow fishing behavior inside biologically valuable OBIS context."""
    level = (v.bio_risk or "").lower()
    if level not in {"high", "medium"}:
        return None

    slow_fishing = (
        2.0 <= v.speed_knots <= 5.0
        or v.behavior in {"trawling", "loitering"}
        or v.loitering_hours >= 2.0
    )
    fishing_type = any(
        key in (v.vessel_type or "").lower()
        for key in ("trawler", "longliner", "seiner", "fishing")
    )
    if not (slow_fishing or fishing_type):
        return None

    species = (
        v.bio_threatened_species
        or v.bio_cetaceans
        or v.bio_sea_turtles
        or v.bio_sharks_rays
    )
    sample = ", ".join(species[:3]) if species else f"{v.bio_species_count} OBIS species"
    if level == "high":
        return RiskReason(
            points=18,
            label="Bio-Risiko-Zone",
            detail=(
                "Schiff zeigt Fischerei-/Loitering-Verhalten in einem OBIS-Gebiet "
                f"mit hoher biologischer Relevanz ({sample})."
            ),
        )
    return RiskReason(
        points=10,
        label="Arten-Hotspot",
        detail=(
            "Schiff bewegt sich fischereitypisch in einem OBIS-Gebiet mit "
            f"sensiblen Arten ({sample})."
        ),
    )


def rule_ghost_ship_remote_sensing(v: Vessel) -> Optional[RiskReason]:
    """Satellite sees a hull/light cluster where AIS has no active match."""
    if v.dark_detection_count <= 0:
        return None
    dist = (
        f"{v.nearest_dark_detection_nm:.1f} nm entfernt"
        if v.nearest_dark_detection_nm >= 0
        else "im lokalen Suchfenster"
    )
    sources = ", ".join(v.dark_detection_sources) if v.dark_detection_sources else "satellite"
    points = 45 if "sar" in v.dark_detection_sources else 35
    return RiskReason(
        points=points,
        label="Ghost-Ship-Detektion",
        detail=(
            f"Nicht-kollaborative Satellitendetektion ({sources}) {dist}, "
            "ohne zeitnahen AIS-Ping in derselben Rasterzelle. "
            "Das ist ein direkter Hinweis auf ein Schiff ohne aktiven "
            "AIS-Transponder; SAR gilt dabei als besonders starkes Signal, "
            "weil es auch bei Nacht und Bewoelkung funktioniert."
        ),
        evidence_type="hard",
    )


# Die Regel-Registry. NEUE REGELN werden ausschliesslich hier eingetragen -
# kein anderer Code muss angefasst werden.
RULES: List[Rule] = [
    rule_protected_area,
    rule_fishing_speed,
    rule_ais_gap,
    rule_loitering,
    rule_flag_of_convenience,
    rule_behavior_profile,
    rule_trajectory_pattern,
    rule_pair_transshipment,
    rule_kinematic_violation,
    rule_static_coords,
    # Contextual fusion (environment + registry):
    rule_thermal_front_loitering,
    rule_weather_suppression,
    rule_historical_offender,
    rule_type_mismatch,
    rule_bio_risk_fishing,
    rule_ghost_ship_remote_sensing,
    # Spatial / geofencing rules (proximity, skirting, sustained zone presence):
    rule_mpa_proximity,
    rule_time_in_mpa,
    rule_border_skirting,
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
        signal_dark_partner_inferred,
    )
    return [
        signal_remote_reefer,
        signal_rendezvous,
        signal_mpa_reefer,
        signal_port_evasion,
        signal_dark_fleet_proximity,
        signal_dark_partner_inferred,
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
    score = max(0.0, min(raw_score, SCORE_CAP))
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

    score = max(0.0, min(raw_score, SCORE_CAP))
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
