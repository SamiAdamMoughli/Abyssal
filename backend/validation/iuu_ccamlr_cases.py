"""Behaviorale Approximationen fuer alle 20 offiziellen IUU-Schiffe (iuu_official.json).

Zweck: Vessel-Objekte fuer jeden Eintrag der offiziellen IUU-Liste bauen, damit
der Score fuer alle bekannten Positiv-Labels geprueft werden kann.

=== EHRLICHKEIT / DATENLAGE ===
Fuer die meisten CCAMLR-Schiffe sind keine oeffentlich zugaenglichen AIS-Traces
verfuegbar. Die Eintraege fallen in zwei Kategorien:

  A) DOKUMENTIERTE FAELLE (5 Schiffe):
     Thunder, Viking, Asian Warrior/Kunlun, STS-50 - extensive oeffentliche
     Berichte (Sea Shepherd, Interpol, CCAMLR). Werte aus bekannten Quellen
     approximiert und im Quellcode entsprechend markiert.

  B) STANDARD-APPROXIMATIONEN (15 Schiffe):
     Keine schiffsspezifischen Berichte zugaenglich. Verhalten gemaess dem
     typischen Muster von CCAMLR-gelisteten Toothfish-Poachern approximiert
     (Longliner im Suedpolarmeer, going dark, fisching in CCAMLR-Gewaessern).
     WICHTIG: Dieser hohe Recall bei Typ-B ist KEIN echter Validierungsbeweis -
     er zeigt nur, dass die Engine generische IUU-Verhaltensmuster erkennt.

Jeder Wert ist als # verified / # approximate / # unknown markiert.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.risk_engine import Vessel


@dataclass
class CCAMLRCase:
    """Ein offizieller IUU-Eintrag mit behavioralem Vessel-Objekt fuer den Score-Test."""

    vessel: Vessel
    imo: str
    listing_year: int
    status: str       # "listed" | "delisted"
    data_quality: str # "documented" | "synthetic"
    sources: str
    notes: str


# --------------------------------------------------------------------------- #
# Standard-Approximation fuer undokumentierte Toothfish-Poacher (Typ B)
# --------------------------------------------------------------------------- #
# Grundlage: CCAMLR Compliance Reports; Sea Shepherd "Operation Icefish"
# (allgemeine Beschreibung von Schiffsverhalten in CCAMLR-Gewaessern);
# TMT "Combined IUU Vessel List" Verhaltensbeschreibungen.
#
# in_protected_area=True  # approximate: alle gelisteten Schiffe wurden fuer
#                           Aktivitaeten in CCAMLR-regulierten Gewaessern gelistet.
# speed_knots=3.0          # approximate: typisches Toothfish-Longliner-Tempo
#                           waehrend Fischerei (Setzen/Holen Grundleine: 2-4 kn).
# ais_gap_hours=18.0       # approximate: IUU-Schiffe in CCAMLR-Gewaessern schalten
#                           AIS regelmaessig ab (dokumentiert in CCAMLR/TMT Berichten).
#                           18h ist ein konservativer Mittelwert.
# loitering_hours=15.0     # approximate: Toothfish-Longliner setzen Gear typisch
#                           12-24h; Hol+Satz-Zyklen erfordern langes Verweilen.
# flag="UNK"               # from CCAMLR list: "Unknown" (CCAMLR veroeffentlicht
#                           keine Flaggen fuer die meisten Eintraege)
# sanctions_check=False    # CCAMLR-Schiffe sind IUU-Fischereifahrzeuge, keine
#                           sanktionierten Tanker. Separates Signal, kein Overlap erwartet.
_STD = dict(
    lat=-60.0, lon=0.0,
    speed_knots=3.0,
    in_protected_area=True,
    ais_gap_hours=18.0,
    flag="UNK",
    loitering_hours=15.0,
    vessel_type="fishing",
    sanctions_check=False,
)

CCAMLR_CASES: list[CCAMLRCase] = [

    # ==================================================================== #
    # TYP A: DOKUMENTIERTE FAELLE (spezifische Quellen und Berichte)
    # ==================================================================== #

    CCAMLRCase(
        vessel=Vessel(
            mmsi="n/a-THUNDER", name="Thunder",
            lat=-57.0, lon=0.0,
            speed_knots=3.0,        # approximate: Longliner-Tempo; Sea Shepherd Logs
            in_protected_area=True, # approximate: CCAMLR-Suedpolarmeer
            ais_gap_hours=48.0,     # approximate: regelmaessig AIS aus; 110-Tage-Verfolgung zeigt lange Dunkelperioden
            flag="NGA",             # verified: zuletzt Nigeria; Interpol/Sea Shepherd berichten
            loitering_hours=10.0,   # approximate: Gear-Satz/Hol-Zyklen; Sea Shepherd Sichtungen
            vessel_type="fishing",
            sanctions_check=False,
        ),
        imo="6905408", listing_year=2006, status="delisted",
        data_quality="documented",
        sources="Sea Shepherd Operation Icefish 2015 (https://www.seashepherd.org/campaigns/operation-icefish/); "
                "Interpol Purple Notice 2013; I. Urbina 'The Outlaw Ocean' (2019); "
                "IMO verifiziert: iuu-vessels.org/Vessel/GetVessel/1682340a-5cea-4491-b688-6b3c5c090c4d",
        notes="Bandit 6; 110 Tage Verfolgung durch Bob Barker; gesunken 5.4.2015. "
              "AIS-Abschaltungen ausfuehrlich dokumentiert.",
    ),

    CCAMLRCase(
        vessel=Vessel(
            mmsi="n/a-VIKING", name="Viking",
            lat=-55.0, lon=70.0,
            speed_knots=3.5,        # approximate: typisches Fischtempo; Interpol-Berichte
            in_protected_area=True, # approximate: CCAMLR-Suedindik-Gewaesser
            ais_gap_hours=24.0,     # approximate: AIS-Manipulation dokumentiert; 'ghost ship' bekannt
            flag="UNK",             # unknown: 12 Flaggen in 13+ Jahren; letzte unbekannt
            loitering_hours=8.0,    # approximate: Gear-Zyklen geschaetzt
            vessel_type="fishing",
            sanctions_check=False,
        ),
        imo="8713392", listing_year=2004, status="delisted",
        data_quality="documented",
        sources="Interpol Purple Notice (erster Fischerei-Purple-Notice 2013); "
                "Indonesia KKP (versenkt 14.3.2016, Pangandaran); "
                "COLTO (https://www.colto.org/2016/02/29/last-toothfish-poacher-viking-arrested-in-indonesia/); "
                "IMO verifiziert: iuu-vessels.org/Vessel/GetVessel/ddc12e4c-0ab3-46fd-b661-d9c3b88c636f",
        notes="Bandit 6; 13 Namen, 12 Flaggen; von Interpol als 'most wanted ship' bezeichnet. "
              "AIS-Abschaltungen ('ghost ship') central fuer Identitaet.",
    ),

    CCAMLRCase(
        vessel=Vessel(
            mmsi="n/a-KUNLUN", name="Asian Warrior",
            lat=-60.0, lon=80.0,
            speed_knots=2.5,        # approximate: Fischerei-Tempo; CCAMLR-Sichtung 2014/15
            in_protected_area=True, # verified: CCAMLR-Suedpolarmeer; Listing-Begruendung
            ais_gap_hours=36.0,     # approximate: AIS-Manipulation; Interpol Purple Notice 2015
            flag="GNQ",             # verified: Aequatorialguinea; Interpol Purple Notice
            loitering_hours=7.0,    # approximate: Gear-Zyklen
            vessel_type="fishing",
            sanctions_check=False,
        ),
        imo="7322897", listing_year=2003, status="listed",
        data_quality="documented",
        sources="Interpol Purple Notice 2015 (AIS-Manipulation, multiple Identitaeten); "
                "CCAMLR IUU-Liste (Alias: Kunlun, Taishan, Chang Bai, 15+ Namen); "
                "Operation Icefish (Sea Shepherd 2014/15)",
        notes="Bandit 6 als 'Kunlun' bekannt. Mindestens 15 Aliasse dokumentiert. "
              "AIS-Manipulation und Flaggenwechsel beides bestaetigt.",
    ),

    CCAMLRCase(
        vessel=Vessel(
            mmsi="n/a-STS50", name="STS-50",
            lat=-45.0, lon=60.0,
            speed_knots=3.0,        # approximate: Kiemennetz-Fischerei-Tempo
            in_protected_area=True, # approximate: Suedmeer-Operationen; CCAMLR-Listing
            ais_gap_hours=72.0,     # approximate: jahrelang AIS aus; 8 Identitaeten dokumentiert
            flag="TGO",             # approximate: zuletzt Togo; Interpol/Indonesien-Berichte
            loitering_hours=9.0,    # approximate
            vessel_type="fishing",
            sanctions_check=False,
        ),
        imo="8514772", listing_year=2016, status="listed",
        data_quality="documented",
        sources="Interpol (Alias 'Andrey Dolgov'); Indonesia seizure 2018; "
                "widely reported as 'ship of thieves' (8 Identitaeten, mehrere Flaggen)",
        notes="Extremfall: 8 Identitaeten, jahrelange AIS-Dunkelperioden. "
              "Eines der am besten dokumentierten IUU-Schiffe der Welt.",
    ),

    # ==================================================================== #
    # TYP B: STANDARD-APPROXIMATIONEN (undokumentierte Faelle)
    # Alle Werte: # approximate: Standard-CCAMLR-IUU-Verhalten (siehe _STD oben)
    # Es sei denn, abweichende Flags aus der offiziellen Liste verfuegbar.
    # ==================================================================== #

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-7036345", name="Amorinn", **_STD),
        imo="7036345", listing_year=2003, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Aliasse: Iceberg II, Lome, Noemi. Alle Verhaltenswerte synthetisch (Typ B).",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-7236634", name="Antony", **_STD),
        imo="7236634", listing_year=2016, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Aliasse: Urgora, Atlantic Oji Maru No. 33. Alle Werte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-9042001", name="Atlantic Wind", **_STD),
        imo="9042001", listing_year=2004, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26; Operation Icefish (als 'Yongding' bekannt)",
        notes="Bandit 6 als 'Yongding' (Alias). Alias: Zemour 2, Luampa, Yongding. "
              "Verhaltenswerte synthetisch (Typ B).",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-9037537", name="Baroon", **_STD),
        imo="9037537", listing_year=2007, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Aliasse: Lana, Zeus, Triton I. Alle Werte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-6622642", name="Challenge", **_STD),
        imo="6622642", listing_year=2006, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Aliasse: Perseverance, Mila. Alle Werte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-7330399", name="Cobija", **_STD),
        imo="7330399", listing_year=2023, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Juengster Eintrag (2023). Aliasse: Cape Flower, Cape Wrath II. Werte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(
            mmsi="imo-7020126", name="Good Hope",
            lat=-60.0, lon=0.0,
            speed_knots=3.0,
            in_protected_area=True,
            ais_gap_hours=18.0,
            flag="NGA",             # verified: Nigeria; aus CCAMLR-Liste (einzige mit Flagge in Quelle)
            loitering_hours=15.0,
            vessel_type="fishing",
            sanctions_check=False,
        ),
        imo="7020126", listing_year=2007, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26 (Flagge Nigeria angegeben)",
        notes="NGA = Nigeria; in FLAGS_OF_CONVENIENCE -> +20 Punkte. "
              "Einzige in der CCAMLR-Liste mit explizit angegebener Flagge (neben Iran/Angola). "
              "Verhaltenswerte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-7322926", name="Heavy Sea", **_STD),
        imo="7322926", listing_year=2004, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Aliasse: Duero, Julius, Keta, Sherpa Uno. Alle Werte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-6607666", name="Jinzhang", **_STD),
        imo="6607666", listing_year=2006, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Aliasse: Hai Lung, Yele, Ray, Kily, Constant, Tropic, Isla Graciosa. Werte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(
            mmsi="imo-7905443", name="Koosha 4",
            lat=-60.0, lon=0.0,
            speed_knots=3.0,
            in_protected_area=True,
            ais_gap_hours=18.0,
            flag="IRN",             # verified: Iran; aus CCAMLR-Liste
            loitering_hours=15.0,
            vessel_type="fishing",
            sanctions_check=False,
        ),
        imo="7905443", listing_year=2011, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26 (Flagge Iran angegeben)",
        notes="Iran (IRN) nicht in FLAGS_OF_CONVENIENCE der Engine -> kein +20. "
              "Verhaltenswerte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-7388267", name="Limpopo", **_STD),
        imo="7388267", listing_year=2003, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Aliasse: Ross, Alos, Lena, Cap George. Alle Werte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-8808654", name="Nika", **_STD),
        imo="8808654", listing_year=2020, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Neuerer Eintrag (2020); wenig oeffentliche Dokumentation. Werte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(
            mmsi="imo-8808903", name="Northern Warrior",
            lat=-60.0, lon=0.0,
            speed_knots=3.0,
            in_protected_area=True,
            ais_gap_hours=18.0,
            flag="AGO",             # approximate: Angola; aus CCAMLR-Liste
            loitering_hours=15.0,
            vessel_type="fishing",
            sanctions_check=False,
        ),
        imo="8808903", listing_year=2016, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26 (Flagge Angola angegeben)",
        notes="Angola (AGO) nicht in FLAGS_OF_CONVENIENCE -> kein +20. "
              "Aliasse: Millennium, Sip 3. Verhaltenswerte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-5062479", name="Perlon", **_STD),
        imo="5062479", listing_year=2003, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26; Operation Icefish (Bandit 6)",
        notes="Bandit 6. Aliasse: Cherne, Bigaro, Hoking, Sargo, Lugalpesca. "
              "Verhaltenswerte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-9319856", name="Pescacisne 1", **_STD),
        imo="9319856", listing_year=2008, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26; Operation Icefish (Bandit 6 als 'Songhua')",
        notes="Bandit 6 als 'Songhua'. Aliasse: Songhua, Yunnan, Nihewan, Paloma V. "
              "Verhaltenswerte synthetisch.",
    ),

    CCAMLRCase(
        vessel=Vessel(mmsi="imo-7424891", name="Sea Urchin", **_STD),
        imo="7424891", listing_year=2007, status="listed",
        data_quality="synthetic",
        sources="CCAMLR IUU-Liste 2025/26",
        notes="Aliasse: Aldabra, Omoa I. Alle Werte synthetisch.",
    ),
]
