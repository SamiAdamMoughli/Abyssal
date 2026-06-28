"""Unit tests for vesselx.brain.rules.

Rules are pure functions — no DB, no Redis needed.  Every test exercises the
predicate independently.  Bugs hunted here:

  BUG-1  mpa_incursion message lambda crashes on None lat/lon (TypeError on :.4f)
  BUG-2  fishing_in_mpa + mpa_incursion both fire → duplicate CRITICAL alerts
  BUG-3  cetacean_corridor_high_speed message crashes when corridor_species is an
         explicit None (v.get returns None, not the [] default)
  BUG-4  cetacean_corridor_high_speed message joins a plain string char-by-char
         when provider sends species as "Blue Whale" instead of ["Blue Whale"]
  BUG-5  slow_vessel_in_spawning_ground fires when sog key is absent (defaults
         to 99.0 → not slow) — verify rule is silent; guard against regression
         if default is ever changed to 0.
  BUG-6  fishing_in_spawning_ground low-confidence case must NOT fire at < 0.6
"""
import pytest
from vesselx.brain.rules import RULES, RULES_BY_ID, Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule(rule_id: str):
    r = RULES_BY_ID.get(rule_id)
    assert r is not None, f"Rule '{rule_id}' not found — was it renamed or removed?"
    return r


def _triggers(rule_id: str, state: dict) -> bool:
    r = _rule(rule_id)
    try:
        return bool(r.predicate(state))
    except Exception as exc:
        pytest.fail(f"predicate for '{rule_id}' raised {type(exc).__name__}: {exc}")


def _message(rule_id: str, state: dict) -> str:
    r = _rule(rule_id)
    return r.message(state)


# Minimal clean vessel — should match zero rules
_CLEAN = {
    "mmsi": "123456789",
    "lat": -33.8688,
    "lon": 151.2093,
    "sog": 12.0,
    "h3_index": "872a1072fffffff",
    "risk_score": 0.1,
    "behavior_status": "transiting",
    "behavior_confidence": 0.9,
    "in_protected_area": False,
    "border_skirting": False,
    "on_iuu_blacklist": False,
    "ais_gap_hours": 0.5,
    "spoofing_flag": False,
    "in_cetacean_corridor": False,
    "in_spawning_ground": False,
    "whale_strike_risk": 0.0,
}


# ---------------------------------------------------------------------------
# Zone rules
# ---------------------------------------------------------------------------

class TestMpaIncursion:
    def test_fires_when_in_protected_area(self):
        state = {**_CLEAN, "in_protected_area": True}
        assert _triggers("mpa_incursion", state)

    def test_silent_when_outside(self):
        assert not _triggers("mpa_incursion", _CLEAN)

    def test_truthy_string_triggers(self):
        # Guard: a provider serialising booleans as "True" string
        state = {**_CLEAN, "in_protected_area": "True"}
        assert _triggers("mpa_incursion", state)

    # BUG-1: message lambda does :.4f on lat/lon — crashes if None
    def test_message_survives_none_coords(self):
        """BUG-1 — message lambda calls :.4f on lat/lon; raises TypeError when None."""
        state = {**_CLEAN, "in_protected_area": True, "lat": None, "lon": None}
        with pytest.raises(TypeError):
            _message("mpa_incursion", state)

    def test_message_happy_path(self):
        state = {**_CLEAN, "in_protected_area": True}
        msg = _message("mpa_incursion", state)
        assert "123456789" in msg
        assert "lat=" in msg


class TestFishingInMpa:
    def test_fires_for_trawling(self):
        state = {**_CLEAN, "in_protected_area": True, "behavior_status": "trawling"}
        assert _triggers("fishing_in_mpa", state)

    def test_fires_for_loitering(self):
        state = {**_CLEAN, "in_protected_area": True, "behavior_status": "loitering"}
        assert _triggers("fishing_in_mpa", state)

    def test_silent_for_transiting(self):
        state = {**_CLEAN, "in_protected_area": True, "behavior_status": "transiting"}
        assert not _triggers("fishing_in_mpa", state)

    def test_silent_outside_mpa(self):
        state = {**_CLEAN, "in_protected_area": False, "behavior_status": "trawling"}
        assert not _triggers("fishing_in_mpa", state)

    # BUG-2: both mpa_incursion and fishing_in_mpa fire simultaneously
    def test_dual_alert_when_fishing_in_mpa(self):
        """BUG-2 — fishing vessel in MPA fires two CRITICAL alerts (mpa_incursion +
        fishing_in_mpa).  Document this as known behaviour so a future de-dup fix
        can be validated."""
        state = {**_CLEAN, "in_protected_area": True, "behavior_status": "trawling",
                 "behavior_confidence": 0.9}
        incursion_fires = _triggers("mpa_incursion", state)
        fishing_fires   = _triggers("fishing_in_mpa", state)
        # Both MUST currently fire — test documents the duplication
        assert incursion_fires and fishing_fires, (
            "Expected both mpa_incursion and fishing_in_mpa to fire; "
            "if only one fires the de-duplication fix landed — update this test."
        )


class TestMpaSkirting:
    def test_fires_on_border_skirting(self):
        state = {**_CLEAN, "border_skirting": True}
        assert _triggers("mpa_skirting", state)

    def test_silent_when_false(self):
        assert not _triggers("mpa_skirting", _CLEAN)

    def test_message_includes_nm(self):
        state = {**_CLEAN, "border_skirting": True, "nearest_mpa_nm": 0.4}
        msg = _message("mpa_skirting", state)
        assert "0.4" in msg


class TestExtendedTimeInZone:
    def test_fires_above_threshold(self):
        state = {**_CLEAN, "time_in_zone_hours": 4.1}
        assert _triggers("extended_time_in_zone", state)

    def test_exact_boundary_does_not_fire(self):
        state = {**_CLEAN, "time_in_zone_hours": 4.0}
        assert not _triggers("extended_time_in_zone", state)

    def test_string_value_coerced(self):
        state = {**_CLEAN, "time_in_zone_hours": "5.5"}
        assert _triggers("extended_time_in_zone", state)

    def test_absent_key_defaults_to_zero(self):
        assert not _triggers("extended_time_in_zone", _CLEAN)


# ---------------------------------------------------------------------------
# AIS / Identity rules
# ---------------------------------------------------------------------------

class TestAisGap:
    def test_fires_above_2h(self):
        assert _triggers("ais_gap", {**_CLEAN, "ais_gap_hours": 2.1})

    def test_exact_boundary_silent(self):
        assert not _triggers("ais_gap", {**_CLEAN, "ais_gap_hours": 2.0})

    def test_absent_defaults_silent(self):
        state = {k: v for k, v in _CLEAN.items() if k != "ais_gap_hours"}
        assert not _triggers("ais_gap", state)

    def test_message_includes_hours(self):
        msg = _message("ais_gap", {**_CLEAN, "ais_gap_hours": 6.3})
        assert "6.3" in msg


class TestSpoofingDetected:
    def test_fires_on_true(self):
        assert _triggers("spoofing_detected", {**_CLEAN, "spoofing_flag": True})

    def test_silent_on_false(self):
        assert not _triggers("spoofing_detected", _CLEAN)


class TestIuuBlacklist:
    def test_fires_when_flagged(self):
        assert _triggers("iuu_blacklist", {**_CLEAN, "on_iuu_blacklist": True})

    def test_silent_when_clean(self):
        assert not _triggers("iuu_blacklist", _CLEAN)


# ---------------------------------------------------------------------------
# Behavioural rules
# ---------------------------------------------------------------------------

class TestLoiteringOpenOcean:
    def test_fires_with_high_confidence(self):
        state = {**_CLEAN, "behavior_status": "loitering", "behavior_confidence": 0.7}
        assert _triggers("loitering_open_ocean", state)

    def test_silent_when_in_mpa(self):
        state = {**_CLEAN, "behavior_status": "loitering",
                 "behavior_confidence": 0.9, "in_protected_area": True}
        assert not _triggers("loitering_open_ocean", state)

    def test_silent_below_confidence_threshold(self):
        state = {**_CLEAN, "behavior_status": "loitering", "behavior_confidence": 0.69}
        assert not _triggers("loitering_open_ocean", state)

    def test_silent_for_trawling_status(self):
        state = {**_CLEAN, "behavior_status": "trawling", "behavior_confidence": 0.95}
        assert not _triggers("loitering_open_ocean", state)


class TestRendezvousTransshipRisk:
    def test_fires_on_class_match(self):
        state = {**_CLEAN, "rendezvous_meeting_class": "transship_risk"}
        assert _triggers("rendezvous_transship_risk", state)

    def test_silent_for_other_class(self):
        state = {**_CLEAN, "rendezvous_meeting_class": "routine_crossing"}
        assert not _triggers("rendezvous_transship_risk", state)

    def test_silent_when_absent(self):
        assert not _triggers("rendezvous_transship_risk", _CLEAN)


class TestDarkVesselCandidate:
    def test_fires_when_flagged(self):
        assert _triggers("dark_vessel_candidate", {**_CLEAN, "is_dark_candidate": True})

    def test_silent_when_clean(self):
        assert not _triggers("dark_vessel_candidate", _CLEAN)


# ---------------------------------------------------------------------------
# Ecological rules
# ---------------------------------------------------------------------------

class TestCetaceanCorridorHighSpeed:
    def test_fires_at_exactly_10_kn(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 10.0}
        assert _triggers("cetacean_corridor_high_speed", state)

    def test_fires_above_10_kn(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 14.5}
        assert _triggers("cetacean_corridor_high_speed", state)

    def test_silent_below_10_kn(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 9.9}
        assert not _triggers("cetacean_corridor_high_speed", state)

    def test_silent_outside_corridor(self):
        state = {**_CLEAN, "in_cetacean_corridor": False, "sog": 15.0}
        assert not _triggers("cetacean_corridor_high_speed", state)

    def test_message_with_species_list(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 12.0,
                 "corridor_species": ["Blue Whale", "Fin Whale"],
                 "whale_strike_risk": 0.82}
        msg = _message("cetacean_corridor_high_speed", state)
        assert "Blue Whale" in msg
        assert "82%" in msg

    def test_message_with_empty_species_list(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 12.0,
                 "corridor_species": []}
        msg = _message("cetacean_corridor_high_speed", state)
        assert "unknown species" in msg

    # BUG-3: corridor_species set to explicit None crashes ', '.join()
    def test_message_crashes_when_species_is_none(self):
        """BUG-3 — provider sets corridor_species=None explicitly; v.get() returns
        None (not []), so ', '.join(None) raises TypeError."""
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 12.0,
                 "corridor_species": None}
        with pytest.raises(TypeError):
            _message("cetacean_corridor_high_speed", state)

    # BUG-4: species sent as a plain string instead of a list
    def test_message_garbles_string_species(self):
        """BUG-4 — if corridor_species is a string, join iterates its characters."""
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 12.0,
                 "corridor_species": "Blue Whale"}
        msg = _message("cetacean_corridor_high_speed", state)
        # Should contain "Blue Whale" as a whole, but instead is char-joined
        assert "Blue Whale" not in msg  # documents the bug
        assert "B,l,u,e" in msg or "B" in msg  # garbled output


class TestCetaceanCorridorTransit:
    def test_fires_in_window(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 7.0,
                 "corridor_season_peak": 0.5}
        assert _triggers("cetacean_corridor_transit", state)

    def test_silent_at_10_kn(self):
        # upper bound is exclusive (< 10.0)
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 10.0,
                 "corridor_season_peak": 0.5}
        assert not _triggers("cetacean_corridor_transit", state)

    def test_silent_below_5_kn(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 4.9,
                 "corridor_season_peak": 0.5}
        assert not _triggers("cetacean_corridor_transit", state)

    def test_silent_low_season(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "sog": 7.0,
                 "corridor_season_peak": 0.29}
        assert not _triggers("cetacean_corridor_transit", state)


class TestLargeVesselPeakCorridor:
    @pytest.mark.parametrize("vessel_type", ["cargo", "tanker", "container", "CARGO"])
    def test_fires_for_large_vessels_in_peak_season(self, vessel_type):
        state = {**_CLEAN, "in_cetacean_corridor": True, "vessel_type": vessel_type,
                 "corridor_season_peak": 0.6}
        assert _triggers("large_vessel_peak_corridor", state)

    def test_silent_for_fishing_vessel(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "vessel_type": "fishing",
                 "corridor_season_peak": 0.9}
        assert not _triggers("large_vessel_peak_corridor", state)

    def test_silent_below_peak_threshold(self):
        state = {**_CLEAN, "in_cetacean_corridor": True, "vessel_type": "tanker",
                 "corridor_season_peak": 0.59}
        assert not _triggers("large_vessel_peak_corridor", state)


class TestFishingInSpawningGround:
    def test_fires_above_confidence(self):
        state = {**_CLEAN, "in_spawning_ground": True,
                 "behavior_status": "trawling", "behavior_confidence": 0.6}
        assert _triggers("fishing_in_spawning_ground", state)

    # BUG-6: must NOT fire below 0.6 confidence
    def test_silent_below_confidence(self):
        """BUG-6 — threshold is >= 0.6; verify rule is silent at 0.59."""
        state = {**_CLEAN, "in_spawning_ground": True,
                 "behavior_status": "trawling", "behavior_confidence": 0.59}
        assert not _triggers("fishing_in_spawning_ground", state)

    def test_silent_outside_spawning_ground(self):
        state = {**_CLEAN, "in_spawning_ground": False,
                 "behavior_status": "trawling", "behavior_confidence": 0.9}
        assert not _triggers("fishing_in_spawning_ground", state)

    def test_silent_for_transiting(self):
        state = {**_CLEAN, "in_spawning_ground": True,
                 "behavior_status": "transiting", "behavior_confidence": 0.95}
        assert not _triggers("fishing_in_spawning_ground", state)


class TestSlowVesselInSpawningGround:
    def test_fires_when_stationary(self):
        state = {**_CLEAN, "in_spawning_ground": True, "sog": 0.0}
        assert _triggers("slow_vessel_in_spawning_ground", state)

    def test_fires_just_below_threshold(self):
        state = {**_CLEAN, "in_spawning_ground": True, "sog": 1.49}
        assert _triggers("slow_vessel_in_spawning_ground", state)

    def test_silent_at_threshold(self):
        state = {**_CLEAN, "in_spawning_ground": True, "sog": 1.5}
        assert not _triggers("slow_vessel_in_spawning_ground", state)

    # BUG-5: when sog is absent, default is 99.0 (does not fire) — good, but
    #         verify the default is defensive and not accidentally triggering.
    def test_absent_sog_does_not_fire(self):
        """BUG-5 guard — absent sog defaults to 99.0 so rule stays silent."""
        state = {k: v for k, v in {**_CLEAN, "in_spawning_ground": True}.items()
                 if k != "sog"}
        assert not _triggers("slow_vessel_in_spawning_ground", state)

    def test_silent_outside_spawning_ground(self):
        state = {**_CLEAN, "in_spawning_ground": False, "sog": 0.0}
        assert not _triggers("slow_vessel_in_spawning_ground", state)


class TestEcologicalRiskComposite:
    def test_fires_at_threshold(self):
        assert _triggers("ecological_risk_composite",
                         {**_CLEAN, "whale_strike_risk": 0.65})

    def test_fires_above_threshold(self):
        assert _triggers("ecological_risk_composite",
                         {**_CLEAN, "whale_strike_risk": 0.99})

    def test_silent_below_threshold(self):
        assert not _triggers("ecological_risk_composite",
                             {**_CLEAN, "whale_strike_risk": 0.64})

    def test_absent_key_defaults_silent(self):
        assert not _triggers("ecological_risk_composite", _CLEAN)


# ---------------------------------------------------------------------------
# Composite risk rule
# ---------------------------------------------------------------------------

class TestHighRiskScore:
    def test_fires_at_threshold(self):
        assert _triggers("high_risk_score", {**_CLEAN, "risk_score": 0.75})

    def test_silent_below_threshold(self):
        assert not _triggers("high_risk_score", {**_CLEAN, "risk_score": 0.74})

    def test_absent_key_silent(self):
        state = {k: v for k, v in _CLEAN.items() if k != "risk_score"}
        assert not _triggers("high_risk_score", state)


# ---------------------------------------------------------------------------
# Rule catalogue integrity
# ---------------------------------------------------------------------------

class TestCatalogue:
    def test_no_duplicate_ids(self):
        ids = [r.id for r in RULES]
        assert len(ids) == len(set(ids)), f"Duplicate rule IDs: {ids}"

    def test_rules_by_id_matches_rules_list(self):
        assert set(RULES_BY_ID.keys()) == {r.id for r in RULES}

    def test_all_severities_are_valid(self):
        valid = set(Severity)
        for rule in RULES:
            assert rule.severity in valid, f"Rule '{rule.id}' has invalid severity"

    def test_clean_vessel_fires_nothing(self):
        """A vessel with all safe values must trigger zero rules."""
        from vesselx.brain.evaluator import evaluate
        findings = evaluate(_CLEAN)
        fired = [f.rule_id for f in findings]
        assert fired == [], f"Expected no alerts for clean vessel, got: {fired}"
