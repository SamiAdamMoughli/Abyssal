"""Unit tests for vesselx.brain.evaluator.

Bugs hunted:
  BUG-E1  evaluate() silently swallows a crashing message lambda but still
          produces an AlertFinding — verify the fallback message is used and
          the finding is NOT dropped.
  BUG-E2  evaluate_batch() keyed on mmsi — duplicate MMSI in batch silently
          overwrites first result; last-wins semantics undocumented.
  BUG-E3  evaluate() with an empty state dict must not crash (rules default-
          guard via .get()).
  BUG-E4  AlertFinding.as_dict() roundtrips through ujson cleanly (triggered_at
          must be ISO string, not a datetime object).
"""
import ujson
import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from vesselx.brain.evaluator import AlertFinding, evaluate, evaluate_batch
from vesselx.brain.rules import RULES, Rule, Severity


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CLEAN = {
    "mmsi": "111222333",
    "lat": -34.0,
    "lon": 18.5,
    "sog": 8.0,
    "h3_index": "872a1072fffffff",
    "risk_score": 0.1,
    "behavior_status": "transiting",
    "behavior_confidence": 0.5,
    "in_protected_area": False,
    "border_skirting": False,
    "on_iuu_blacklist": False,
    "ais_gap_hours": 0.0,
    "spoofing_flag": False,
    "in_cetacean_corridor": False,
    "in_spawning_ground": False,
    "whale_strike_risk": 0.0,
}

_IUU_VESSEL = {**_CLEAN, "on_iuu_blacklist": True}
_SPOOFING_VESSEL = {**_CLEAN, "spoofing_flag": True}


# ---------------------------------------------------------------------------
# Basic evaluation
# ---------------------------------------------------------------------------

class TestEvaluateBasic:
    def test_clean_vessel_returns_empty_list(self):
        assert evaluate(_CLEAN) == []

    def test_iuu_vessel_triggers_one_finding(self):
        findings = evaluate(_IUU_VESSEL)
        assert any(f.rule_id == "iuu_blacklist" for f in findings)

    def test_finding_fields_populated(self):
        [f] = [f for f in evaluate(_IUU_VESSEL) if f.rule_id == "iuu_blacklist"]
        assert f.mmsi == "111222333"
        assert f.lat == -34.0
        assert f.lon == 18.5
        assert f.severity == Severity.CRITICAL
        assert isinstance(f.triggered_at, datetime)
        assert f.triggered_at.tzinfo is not None  # must be tz-aware

    def test_multiple_rules_can_fire(self):
        state = {**_CLEAN, "on_iuu_blacklist": True, "spoofing_flag": True,
                 "risk_score": 0.9}
        findings = evaluate(state)
        rule_ids = {f.rule_id for f in findings}
        assert "iuu_blacklist" in rule_ids
        assert "spoofing_detected" in rule_ids
        assert "high_risk_score" in rule_ids

    # BUG-E3: empty state must not crash the evaluator
    def test_empty_state_does_not_raise(self):
        findings = evaluate({})
        assert isinstance(findings, list)

    def test_none_values_do_not_crash_predicates(self):
        """Predicates use .get() with defaults; explicit None values should not crash."""
        state = {
            "mmsi": None, "lat": None, "lon": None, "sog": None,
            "risk_score": None, "ais_gap_hours": None,
        }
        findings = evaluate(state)
        assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# BUG-E1: crashing message lambda falls back gracefully
# ---------------------------------------------------------------------------

class TestMessageFallback:
    def test_crashing_message_produces_fallback_not_dropped(self):
        """BUG-E1 — if the message lambda raises, evaluator must use the fallback
        string and still emit the finding (not silently drop it)."""
        boom_rule = Rule(
            id="test_boom",
            label="Always Fires Boom",
            severity=Severity.WARNING,
            predicate=lambda v: True,
            message=lambda v: (_ for _ in ()).throw(RuntimeError("deliberate")),
        )
        with patch("vesselx.brain.evaluator.RULES", [boom_rule]):
            findings = evaluate(_CLEAN)

        assert len(findings) == 1
        assert findings[0].rule_id == "test_boom"
        assert "Always Fires Boom" in findings[0].message  # fallback text
        assert "111222333" in findings[0].message


# ---------------------------------------------------------------------------
# BUG-E2: evaluate_batch last-wins on duplicate MMSI
# ---------------------------------------------------------------------------

class TestEvaluateBatch:
    def test_batch_returns_dict_keyed_by_mmsi(self):
        states = [_CLEAN, {**_CLEAN, "mmsi": "999888777"}]
        result = evaluate_batch(states)
        assert set(result.keys()) == {"111222333", "999888777"}

    def test_batch_duplicate_mmsi_last_wins(self):
        """BUG-E2 — when two states share the same MMSI the dict comprehension
        keeps only the last one.  Document this: the first state (IUU) is
        silently overwritten by the second (clean), producing zero alerts."""
        dirty = {**_CLEAN, "on_iuu_blacklist": True}
        clean = {**_CLEAN}  # same MMSI, no flags
        result = evaluate_batch([dirty, clean])
        # Documents last-wins: result shows no IUU alert despite dirty being first
        iuu_alerts = [f for f in result.get("111222333", [])
                      if f.rule_id == "iuu_blacklist"]
        assert iuu_alerts == [], (
            "Last-wins semantics: IUU alert from first entry is silently lost. "
            "Fix evaluate_batch to accumulate findings across duplicate MMSIs."
        )

    def test_batch_empty_input(self):
        assert evaluate_batch([]) == {}

    def test_batch_missing_mmsi_key(self):
        """State without mmsi key goes under empty-string bucket."""
        state = {"lat": 1.0, "lon": 1.0}
        result = evaluate_batch([state])
        assert "" in result


# ---------------------------------------------------------------------------
# BUG-E4: AlertFinding.as_dict() serialises to ujson-safe types
# ---------------------------------------------------------------------------

class TestAlertFindingAsDict:
    def test_as_dict_is_json_serialisable(self):
        """BUG-E4 — triggered_at must be an ISO string, not a datetime; ujson
        cannot serialise datetime objects and will raise TypeError."""
        findings = evaluate({**_CLEAN, "on_iuu_blacklist": True})
        assert findings
        blob = ujson.dumps(findings[0].as_dict())  # must not raise
        parsed = ujson.loads(blob)
        assert parsed["rule_id"] == "iuu_blacklist"
        assert isinstance(parsed["triggered_at"], str)

    def test_as_dict_severity_is_string(self):
        """Severity enum must be serialised as its .value string."""
        findings = evaluate({**_CLEAN, "on_iuu_blacklist": True})
        d = findings[0].as_dict()
        assert isinstance(d["severity"], str)
        assert d["severity"] == "critical"

    def test_as_dict_all_keys_present(self):
        findings = evaluate({**_CLEAN, "on_iuu_blacklist": True})
        d = findings[0].as_dict()
        required = {"alert_id", "rule_id", "rule_label", "severity",
                    "message", "mmsi", "lat", "lon", "h3_index", "triggered_at"}
        assert required <= set(d.keys())
