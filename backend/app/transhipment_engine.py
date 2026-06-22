"""Mission Radar - Transhipment Detection Engine.

Schliesst die strukturelle Luecke die der Validierungsreport identifiziert hat:
Reefer-/Carrier-Schiffe die illegal gefangenen Fisch auf See uebernehmen
entgehen dem klassischen Fischerei-Score komplett (kein Fischtempo, kein
AIS-Gap) - aber hinterlassen andere, erkennbare Muster.

Designprinzip: Jedes Signal erklaerbar, jede Schwelle quellenbasiert.
Alle Signale Optional[RiskReason] -> gleicher Vertrag wie risk_engine.RULES.

Quellen (peer-reviewed / behoerdlich wo moeglich):
  GFW Encounter-Events:   doi.org/10.1126/sciadv.abb3887 (Kroodsma et al. 2018)
  UNODC Transhipment:     UNODC "Transhipment at Sea" 2023
  Port State Control:     Paris MOU Annual Report 2023 (Inspektion = Hafen-Call)
  GFW "Dark Vessels":     GFW "Illuminating Dark Fishing Fleets" 2021
  FAO Transhipment Guide: FAO Fisheries Technical Paper 622 (2019)
"""

from __future__ import annotations

from typing import Optional

from .risk_engine import RiskReason, Vessel

# Vessel-Typ-Cluster (lowercase, nach Normalisierung in gfw_vessels/_normalize_vessel_type)
_REEFER_TYPES = {"reefer", "carrier", "refrigerated"}
_FISHING_TYPES = {"fishing", "trawler", "longliner", "purse_seiner", "reefer"}
_EVASION_TYPES = {"reefer", "carrier", "refrigerated", "fishing",
                  "trawler", "longliner", "purse_seiner"}


def _vtype(v: Vessel) -> str:
    """Normalisierter Vessel-Typ (lowercase, leer wenn unklar)."""
    return (v.vessel_type or "").strip().lower()


# --------------------------------------------------------------------------- #
# Signal 1: Remote Reefer Operation
# --------------------------------------------------------------------------- #
# Quelle: FAO Fisheries Technical Paper 622 (2019) - legitime Reefer-Schiffe
#   operieren auf Handelsrouten zwischen Haefen und haben selten >7 Tage
#   Abstand zum naechsten Hafen ohne Zwischenstopp. 200 nm und 7 Tage sind
#   konservative Schwellen (echte Transshipment-Faelle zeigen >500 nm / >30 Tage).

def signal_remote_reefer(v: Vessel) -> Optional[RiskReason]:
    """Kuehlschiff weit von jedem Hafen - kein wirtschaftlicher Grund."""
    if _vtype(v) not in _REEFER_TYPES:
        return None
    if v.distance_to_nearest_port_nm < 0 or v.days_since_port < 0:
        return None  # unbekannte Werte -> kein Signal (konservativ)
    if v.distance_to_nearest_port_nm > 200 and v.days_since_port > 7:
        return RiskReason(
            points=30,
            label="Remote Reefer Operation",
            detail=(
                f"Kuehlschiff seit {v.days_since_port:.0f} Tagen auf See, "
                f"{v.distance_to_nearest_port_nm:.0f} nm vom naechsten Hafen. "
                "Kein wirtschaftlicher Grund fuer diese Position abseits von "
                "Handelsrouten. Klassisch fuer illegale Fischuebernahme auf See."
            ),
            evidence_type="behavioral",
        )
    return None


# --------------------------------------------------------------------------- #
# Signal 2: Active Rendezvous Pattern
# --------------------------------------------------------------------------- #
# Quelle: Kroodsma et al. (2018) doi.org/10.1126/sciadv.abb3887 - GFW-Encounter-
#   Events (< 0.5 nm, beide Schiffe < 3 kn) sind der staerkste publizierte
#   Transhipment-Indikator aus AIS-Daten. Schwelle 0.5 h ist bewusst niedrig
#   (echte Transhipments dauern 1-8 h; 30 min ist Untergrenze).
#   Evidence: "hard" - Encounter-Events sind direkt beobachtbare AIS-Signale.

def signal_rendezvous(v: Vessel) -> Optional[RiskReason]:
    """Aktives Rendezvous-Muster: stationaer nahe Fischereifahrzeug."""
    if v.rendezvous_duration_hours < 0.5:
        return None
    if v.nearby_fishing_vessels < 1:
        return None
    if v.speed_knots >= 3.0:
        return None  # zu schnell fuer aktiven Transfer
    return RiskReason(
        points=35,
        label="Sea Transhipment Rendezvous",
        detail=(
            f"Schiff {v.rendezvous_duration_hours:.1f} h stationaer "
            f"(< 0.5 nm) neben {v.nearby_fishing_vessels} Fischereifahrzeug(en). "
            "Klassische AIS-Signatur einer Uebergabe auf See (GFW Encounter Event). "
            "Quelle: Kroodsma et al. 2018, doi.org/10.1126/sciadv.abb3887"
        ),
        evidence_type="hard",
    )


# --------------------------------------------------------------------------- #
# Signal 3: Reefer in MPA — Anomalous
# --------------------------------------------------------------------------- #
# Quelle: UNODC "Transhipment at Sea" 2023 - Kuehlschiffe haben keinen legalen
#   Betriebsgrund in Meeresschutzgebieten. Gleichzeitig zeigen Fallstudien
#   (z.B. Ross Sea MPA, Galapagos Marine Reserve), dass illegale Fischer
#   Schutzgebiete aufsuchen und dort Ubergaben arrangieren.
#   Evidence: "hard" - Schutzgebiet-Status ist verifiziertes geographisches Faktum.

def signal_mpa_reefer(v: Vessel) -> Optional[RiskReason]:
    """Kuehlschiff in einem Meeresschutzgebiet - keine legitime Erklaerung."""
    if _vtype(v) not in _REEFER_TYPES:
        return None
    if not v.in_protected_area:
        return None
    return RiskReason(
        points=25,
        label="Reefer in MPA — Anomalous",
        detail=(
            "Kuehlschiff innerhalb eines Meeresschutzgebiets (MPA). "
            "Legitime Reefer-Schiffe haben keinen Betriebsgrund in MPAs. "
            "Deutet auf Unterstuetzung illegaler Fischerei in Schutzgewaessern hin. "
            "(UNODC Transhipment at Sea, 2023)"
        ),
        evidence_type="hard",
    )


# --------------------------------------------------------------------------- #
# Signal 4: Port Call Evasion
# --------------------------------------------------------------------------- #
# Quelle: Paris MOU Annual Report 2023 - Port State Control Inspektionen finden
#   ausschliesslich in Haefen statt. 30+ Tage ohne Hafen-Call bei Null bekannten
#   Calls = aktive Inspektionsvermeidung. Schwelle 30 Tage (FAO 622 nennt
#   "normale" Reefer-Routen: alle 14-21 Tage Hafen).
#   Nur bei verifizierten 0 Calls (recent_port_calls == 0) - unbekannte Werte (-1)
#   werden als "keine Daten" behandelt und loesen kein Signal aus.

def signal_port_evasion(v: Vessel) -> Optional[RiskReason]:
    """30+ Tage auf See, keine Hafen-Calls - aktive Inspektionsvermeidung."""
    if _vtype(v) not in _EVASION_TYPES:
        return None
    if v.days_since_port < 30:
        return None
    if v.recent_port_calls != 0:  # -1 = unbekannt -> kein Signal (konservativ)
        return None
    return RiskReason(
        points=20,
        label="Port Call Evasion",
        detail=(
            f"{v.days_since_port:.0f} Tage auf See ohne Hafen-Call. "
            "Port State Control (Paris/Tokyo MOU) inspiziert NUR in Haefen - "
            "30+ Tage ohne Hafen = aktive Inspektion-Vermeidung. "
            "(Paris MOU Annual Report 2023)"
        ),
        evidence_type="behavioral",
    )


# --------------------------------------------------------------------------- #
# Signal 5: Dark Fleet Aggregation
# --------------------------------------------------------------------------- #
# Quelle: GFW "Illuminating Dark Fishing Fleets" 2021; UNODC 2023 - Illegale
#   Flottenverbande zeigen charakteristisches Bild: mehrere Fischerfahrzeuge
#   aggregieren weit von Haefen, oft mit einem nicht-fischenden Support-Schiff
#   (Versorger, Carrier). > 500 nm von Hafen und < 2 kn (praktisch stationaer)
#   mit 3+ Fischern in der Naehe ist ein starkes Indikator-Cluster.
#   Schwelle 2.0 kn (strikter als Signal 2): nur bei nahezu vollem Stillstand.

def signal_dark_fleet_proximity(v: Vessel) -> Optional[RiskReason]:
    """Stationaer umgeben von 3+ Fischern, weit von jedem Hafen."""
    if v.nearby_fishing_vessels < 3:
        return None
    if v.distance_to_nearest_port_nm < 500:
        return None
    if v.speed_knots >= 2.0:
        return None
    return RiskReason(
        points=30,
        label="Dark Fleet Aggregation",
        detail=(
            f"Stationaeres Schiff umgeben von {v.nearby_fishing_vessels} "
            f"Fischereifahrzeugen, {v.distance_to_nearest_port_nm:.0f} nm "
            "vom naechsten Hafen. Konsistent mit illegalem Flottenverband "
            "und Support-Schiff-Rolle. "
            "(GFW 'Dark Fishing Fleets' 2021; UNODC 2023)"
        ),
        evidence_type="hard",
    )


# --------------------------------------------------------------------------- #
# Compound Multiplier Label (wird in compound_score() als Marker-Reason gesetzt)
# --------------------------------------------------------------------------- #
COMPOUND_MULTIPLIER = 1.4
COMPOUND_TRIGGER_LABELS = {"Remote Reefer Operation", "Sea Transhipment Rendezvous"}

COMPOUND_LABEL = "TRANSHIPMENT COMPOUND x1.4"


def compound_explanation(raw: float) -> RiskReason:
    """Erklaerungsreason fuer den Compound-Multiplier (points=0, nur Dokumentation)."""
    return RiskReason(
        points=0,
        label=COMPOUND_LABEL,
        detail=(
            f"Compound-Multiplier x{COMPOUND_MULTIPLIER}: Remote Reefer + "
            f"Aktives Rendezvous gleichzeitig erkannt. Roh-Score {raw:.0f} "
            f"-> {min(raw * COMPOUND_MULTIPLIER, 100.0):.0f} (Cap 100). "
            "Synergieeffekt: Reefer weit von Routen + aktiver Transfer = "
            "sehr hohe Transhipment-Konfidenz."
        ),
        evidence_type="compound",
    )
