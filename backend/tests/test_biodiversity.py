from app.risk_engine import Vessel, rule_bio_risk_fishing
from app.sources import biodiversity


def test_classify_records_flags_high_value_species():
    records = [
        {
            "scientificName": "Megaptera novaeangliae",
            "decimalLatitude": -0.5,
            "decimalLongitude": -90.5,
        },
        {
            "scientificName": "Sphyrna lewini",
            "redlistCategory": "CR",
            "decimalLatitude": -0.51,
            "decimalLongitude": -90.49,
        },
    ]

    summary = biodiversity.classify_records(records)

    assert summary["bio_risk"] == "high"
    assert summary["total_species"] == 2
    assert "Megaptera novaeangliae" in summary["cetaceans"]
    assert "Sphyrna lewini" in summary["threatened_species"]


def test_lookup_uses_cached_nearby_records(monkeypatch):
    monkeypatch.setattr(biodiversity, "_records", [
        {
            "scientificName": "Sphyrna lewini",
            "redlistCategory": "EN",
            "decimalLatitude": -0.5,
            "decimalLongitude": -90.5,
        },
        {
            "scientificName": "Caretta caretta",
            "decimalLatitude": 10.0,
            "decimalLongitude": 10.0,
        },
    ])

    summary = biodiversity.lookup(-0.51, -90.49, radius_deg=0.05)

    assert summary["bio_risk"] == "medium"
    assert summary["total_species"] == 1
    assert summary["threatened_species"] == ["Sphyrna lewini"]


def test_bio_risk_rule_requires_fishing_context():
    vessel = Vessel(
        mmsi="1",
        name="Test Longliner",
        lat=-0.5,
        lon=-90.5,
        speed_knots=3.2,
        vessel_type="longliner",
        bio_risk="high",
        bio_threatened_species=["Sphyrna lewini"],
    )

    reason = rule_bio_risk_fishing(vessel)

    assert reason is not None
    assert reason.points == 18
    assert "Bio-Risiko" in reason.label


def test_bio_risk_rule_ignores_fast_transit():
    vessel = Vessel(
        mmsi="2",
        name="Fast Transit",
        lat=-0.5,
        lon=-90.5,
        speed_knots=14.0,
        vessel_type="container",
        bio_risk="high",
        bio_threatened_species=["Sphyrna lewini"],
    )

    assert rule_bio_risk_fishing(vessel) is None
