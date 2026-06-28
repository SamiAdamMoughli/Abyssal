from app.risk_engine import Vessel, rule_ghost_ship_remote_sensing
from app.sources import dark_vessels


def test_detect_ghost_ships_requires_missing_ais_match():
    detections = [
        {
            "id": "sar-unmatched",
            "lat": -0.50,
            "lon": -90.50,
            "timestamp": "2026-06-26T02:00:00Z",
            "sensor": "sentinel-1-sar",
            "confidence": 0.92,
        },
        {
            "id": "sar-matched",
            "lat": -0.25,
            "lon": -90.25,
            "timestamp": "2026-06-26T02:00:00Z",
            "sensor": "sentinel-1-sar",
        },
    ]
    ais_pings = [
        {
            "mmsi": "123456789",
            "lat": -0.25,
            "lon": -90.25,
            "timestamp": "2026-06-26T02:10:00Z",
        }
    ]

    ghosts = dark_vessels.detect_ghost_ships(
        detections,
        ais_pings,
        resolution=7,
        match_window_minutes=15,
    )

    assert [g["id"] for g in ghosts] == ["sar-unmatched"]
    assert ghosts[0]["source_type"] == "sar"
    assert ghosts[0]["ais_matches"] == 0


def test_ais_ping_outside_time_window_does_not_suppress_detection():
    ghosts = dark_vessels.detect_ghost_ships(
        [{
            "id": "viirs-light",
            "lat": -0.5,
            "lon": -90.5,
            "timestamp": "2026-06-26T02:00:00Z",
            "sensor": "viirs",
        }],
        [{
            "mmsi": "123456789",
            "lat": -0.5,
            "lon": -90.5,
            "timestamp": "2026-06-26T02:30:01Z",
        }],
        match_window_minutes=15,
    )

    assert len(ghosts) == 1
    assert ghosts[0]["source_type"] == "viirs"


def test_ghost_ship_rule_scores_cached_remote_sensing_context():
    vessel = Vessel(
        mmsi="ghost:sar-unmatched",
        name="Unidentified SAR target",
        lat=-0.5,
        lon=-90.5,
        dark_detection_count=1,
        dark_detection_sources=["sar"],
        nearest_dark_detection_nm=0.2,
    )

    reason = rule_ghost_ship_remote_sensing(vessel)

    assert reason is not None
    assert reason.points == 45
    assert reason.evidence_type == "hard"
    assert "Ghost" in reason.label
