"""Bekannte, oeffentlich dokumentierte IUU-Fischerei-Faelle als Testfaelle.

ZWECK: Pruefen, ob die regelbasierte Engine reale, bekannte IUU-Faelle hoch
bewertet haette. Dies ist ein SEPARATES Validierungs-Modul - es ruft die Engine
nur auf, aendert sie nie.

================================ EHRLICHKEIT ================================
Die Schiffe hier sind real und dokumentiert. Die EINGABEWERTE (speed_knots,
ais_gap_hours, loitering_hours, in_protected_area) sind jedoch APPROXIMATIONEN
des dokumentierten Verhaltens - KEINE echten AIS-Traces zum jeweiligen Tatzeit-
punkt. Oeffentliche Berichte beschreiben das Verhalten qualitativ ("ging dark",
"fischte im Sperrgebiet"), nennen aber selten exakte Knoten/Stundenwerte.

Darum gilt fuer jeden Fall:
  - `approximate=True` ist die Regel.
  - Jeder Fall traegt eine `source` (Bericht/URL) zur Nachpruefbarkeit.
  - Wo ein Wert besonders unsicher ist, steht es als Kommentar dabei.
  - Exakte MMSI-Nummern werden NICHT erfunden; wo nicht sicher belegbar, steht
    "n/a" und die Identitaet ist ueber Name + Quelle nachvollziehbar.
Lieber konservativ schaetzen und kennzeichnen als raten.
============================================================================
"""

from __future__ import annotations

from dataclasses import dataclass

from app.risk_engine import Vessel


@dataclass
class KnownCase:
    """Ein dokumentierter Fall + erwartetes Label + Quelle."""

    vessel: Vessel
    expected_high_risk: bool
    source: str
    approximate: bool = True
    notes: str = ""


# Hinweis zur Abbildung "Sperrgebiet" -> in_protected_area:
# Die Engine kennt nur den Boolean in_protected_area. Mehrere Faelle spielten in
# CCAMLR-regulierten Gewaessern des Suedpolarmeers (Fischerei stark reguliert /
# in Teilen gesperrt, z. B. Ross-Sea-MPA). Das wird hier konservativ als
# in_protected_area=True abgebildet und als Approximation gekennzeichnet.

KNOWN_CASES: list[KnownCase] = [
    # ----------------------------------------------------------------------- #
    # 1) F/V THUNDER  - "Bandit 6", beruechtigtster Toothfish-Poacher
    # ----------------------------------------------------------------------- #
    KnownCase(
        vessel=Vessel(
            mmsi="n/a-THUNDER",     # exakte MMSI nicht sicher belegbar -> n/a
            name="Thunder",
            lat=-57.0, lon=0.0,     # ca. Suedpolarmeer; Position approximativ
            speed_knots=3.0,        # APPROX: Stell-/Hol-Tempo bei Langleine/Netz
            in_protected_area=True, # APPROX: CCAMLR-reguliertes Gebiet
            ais_gap_hours=48,       # APPROX: schaltete AIS ueber lange Phasen ab
            flag="NGA",             # zuletzt Nigeria; zuvor mehrfach gewechselt
            loitering_hours=10,     # APPROX: langes Setzen/Holen der Gear
        ),
        expected_high_risk=True,
        approximate=True,
        # Sea Shepherd "Operation Icefish" 2014/15 (110-Tage-Verfolgung);
        # Interpol Purple Notice; I. Urbina, "The Outlaw Ocean" (2019).
        source="Sea Shepherd Operation Icefish 2015; Interpol Purple Notice; "
               "Urbina, The Outlaw Ocean (2019). "
               "https://www.seashepherd.org/campaigns/operation-icefish/",
        notes="Verhalten gut dokumentiert (AIS aus, Fischerei im Sperrgebiet); "
              "exakte Knoten/Stunden approximiert.",
    ),
    # ----------------------------------------------------------------------- #
    # 2) F/V VIKING  - "Bandit 6", AIS-Manipulation/Identitaetswechsel
    # ----------------------------------------------------------------------- #
    KnownCase(
        vessel=Vessel(
            mmsi="n/a-VIKING",
            name="Viking",
            lat=-55.0, lon=70.0,    # Suedlicher Indik, approximativ
            speed_knots=3.5,        # APPROX: Fischerei-Tempo
            in_protected_area=True, # APPROX: CCAMLR-reguliertes Gebiet
            ais_gap_hours=24,       # APPROX: AIS-Manipulation/Abschaltung
            flag="NGA",             # haeufig gewechselt / teils staatenlos
            loitering_hours=8,
        ),
        expected_high_risk=True,
        approximate=True,
        # Interpol Purple Notice; 2016 von Indonesien (KKP) gestellt und versenkt.
        source="Interpol Purple Notice (5 Jahre auf der Flucht, mehrfach "
               "umgeflaggt); Indonesia KKP 2016 (vessel sunk). "
               "https://newint.org/features/2016/06/01/end-of-the-line",
        notes="Wechselnde Namen/Flaggen dokumentiert; AIS-Werte approximiert.",
    ),
    # ----------------------------------------------------------------------- #
    # 3) F/V KUNLUN (alias Taishan/Chang Bai)  - "Bandit 6"
    # ----------------------------------------------------------------------- #
    KnownCase(
        vessel=Vessel(
            mmsi="n/a-KUNLUN",
            name="Kunlun",
            lat=-60.0, lon=80.0,    # Suedpolarmeer, approximativ
            speed_knots=2.5,        # APPROX: Fischerei-Tempo
            in_protected_area=True, # APPROX: CCAMLR-Gebiet
            ais_gap_hours=36,       # APPROX
            flag="GNQ",             # u. a. Aequatorialguinea gemeldet
            loitering_hours=7,
        ),
        expected_high_risk=True,
        approximate=True,
        # CCAMLR IUU-Liste; 2015 in Thailand/Senegal-Kontext mehrfach detained;
        # mehrere Identitaeten dokumentiert.
        source="Interpol Purple Notice 2015 (AIS-Manipulation dokumentiert); "
               "CCAMLR IUU vessel list; 2015 detentions (multiple identities). "
               "https://www.interpol.int/en/News-and-Events/News/2015/",
        notes="Identitaetswechsel dokumentiert; AIS-Werte approximiert.",
    ),
    # ----------------------------------------------------------------------- #
    # 4) STS-50 (alias Andrey Dolgov, "Ship of Thieves")
    # ----------------------------------------------------------------------- #
    KnownCase(
        vessel=Vessel(
            mmsi="n/a-STS50",
            name="STS-50 (Andrey Dolgov)",
            lat=-45.0, lon=60.0,    # operierte weit verstreut; approximativ
            speed_knots=3.0,        # APPROX: Kiemennetz-Fischerei
            in_protected_area=True, # APPROX: reguliertes Suedmeer-Gebiet
            ais_gap_hours=72,       # APPROX: jahrelang AIS aus, 8 Identitaeten
            flag="TGO",             # zuletzt Togo; zuvor viele
            loitering_hours=9,
        ),
        expected_high_risk=True,
        approximate=True,
        # Interpol; April 2018 von Indonesien gestellt; "ship of thieves",
        # acht Namen/Flaggen ueber Jahre.
        source="Interpol; Indonesia 2018 seizure; widely reported "
               "(\"ship of thieves\").",
        notes="Extremer Fall fuer AIS-Abschaltung/Identitaetswechsel.",
    ),
    # ----------------------------------------------------------------------- #
    # 5) FU YUAN YU LENG 999  - Reefer, 2017 IM Galapagos-Schutzgebiet gestellt
    #    BEWUSST als schwieriger Fall: ein KUEHL-/TRANSPORTSCHIFF, das NICHT
    #    aktiv fischte, sondern (mit illegaler Hai-Ladung) durch das Reservat
    #    fuhr. Es erfuellt das Fischerei-Tempo NICHT und hatte AIS an - der Score
    #    wird es daher voraussichtlich UNTERbewerten. Das ist Absicht: ein
    #    ehrlicher Test der Grenzen der aktuellen Regeln.
    # ----------------------------------------------------------------------- #
    KnownCase(
        vessel=Vessel(
            mmsi="n/a-FUYUANYU999",
            name="Fu Yuan Yu Leng 999",
            lat=-0.5, lon=-90.9,    # innerhalb Galapagos Marine Reserve
            speed_knots=9.0,        # APPROX: Transit eines Reefers (kein Fischen)
            in_protected_area=True, # belegt: im Galapagos-Reservat gestellt
            ais_gap_hours=2,        # APPROX: u. a. per Radar entdeckt; AIS eher an
            flag="CHN",
            loitering_hours=0,      # transitierte, verweilte nicht
        ),
        expected_high_risk=True,
        approximate=True,
        # Aug 2017, Galapagos Marine Reserve; ~6.600 Haie an Bord; ecuadorian.
        # Gericht verurteilte die Crew. Breit berichtet (BBC, National Geographic).
        source="Aug 2017 Galapagos seizure (~6,600 sharks aboard); Ecuadorian "
               "court ruling; BBC/National Geographic coverage.",
        notes="REEFER/Transport, nicht aktiv fischend -> testet eine bekannte "
              "Schwaeche regelbasierter Speed-/Gap-Logik.",
    ),
    # ----------------------------------------------------------------------- #
    # 6) NEGATIVE KONTROLLE (KEIN realer Fall) - legitimer Transit
    #    Bewusst KONSTRUIERT, klar gekennzeichnet: dient nur dazu, False
    #    Positives zu messen (ein unauffaelliges Schiff darf NICHT hoch scoren).
    # ----------------------------------------------------------------------- #
    KnownCase(
        vessel=Vessel(
            mmsi="CONTROL-LEGIT",
            name="Kontrolle: legitimer Frachter (konstruiert)",
            lat=-1.5, lon=-88.0,    # ausserhalb Schutzgebiet
            speed_knots=13.0,       # klarer Transit, kein Fischerei-Tempo
            in_protected_area=False,
            ais_gap_hours=0,        # AIS durchgehend an
            flag="PAN",
            loitering_hours=0,
        ),
        expected_high_risk=False,
        approximate=False,
        source="KONSTRUIERTE Negativkontrolle - kein realer Fall, kein Beleg "
               "noetig. Dient der Messung von False Positives.",
        notes="Soll niedrig scoren; testet, dass die Engine nicht alles flaggt.",
    ),
]
