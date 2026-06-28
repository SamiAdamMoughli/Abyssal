"""Unit tests for vesselx.gateway.webhook.

Tests run against the normaliser functions directly (no FastAPI TestClient /
Redis needed) plus the HMAC helper.

Bugs hunted:
  BUG-W1  _verify_hmac uses hmac.new — verify the stdlib function exists and
          compare_digest comparison is correct (timing-safe).
  BUG-W2  _normalize_spire silently swallows a record with an invalid latitude
          (e.g. 999.0) — must return None, not a record with bad coords.
  BUG-W3  _normalize_orbcomm GeoJSON coords are [lon, lat] (lon first); verify
          the normaliser does NOT swap them (a swap would place vessels on the
          wrong side of the globe).
  BUG-W4  _normalize_orbcomm with an empty coordinates list ([]) must not crash.
  BUG-W5  _normalize_spire treats missing 'mmsi' key as KeyError → None; verify
          graceful skip rather than propagating to the caller.
  BUG-W6  generic endpoint has no HMAC guard — document that it must NOT be
          exposed on a public interface (test asserts no secret env var needed).
  BUG-W7  _check_signature with a secret configured but a None header must raise
          401, not silently pass.
"""
import hashlib
import hmac
import importlib
import os
import sys
import pytest
from unittest.mock import AsyncMock, patch

# ---------------------------------------------------------------------------
# Import the module under test without triggering FastAPI app construction
# We import the helpers and normalisers directly.
# ---------------------------------------------------------------------------
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[4] / "src"))

from vesselx.gateway.webhook import (
    _check_signature,
    _normalize_orbcomm,
    _normalize_spire,
    _verify_hmac,
)


# ---------------------------------------------------------------------------
# HMAC helpers — BUG-W1, BUG-W7
# ---------------------------------------------------------------------------

class TestVerifyHmac:
    _SECRET = "test-secret-key"
    _BODY   = b'[{"mmsi": "123"}]'

    def _make_sig(self, body: bytes = _BODY, secret: str = _SECRET) -> str:
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    # BUG-W1: confirm hmac.new is valid and produces correct digest
    def test_correct_signature_returns_true(self):
        sig = self._make_sig()
        assert _verify_hmac(self._BODY, sig, self._SECRET) is True

    def test_wrong_secret_returns_false(self):
        sig = self._make_sig(secret="different-secret")
        assert _verify_hmac(self._BODY, sig, self._SECRET) is False

    def test_tampered_body_returns_false(self):
        sig = self._make_sig()
        assert _verify_hmac(b"tampered", sig, self._SECRET) is False

    def test_sha256_prefix_stripped(self):
        sig = "sha256=" + self._make_sig()
        assert _verify_hmac(self._BODY, sig, self._SECRET) is True

    def test_uppercase_sha256_prefix_stripped(self):
        sig = "SHA256=" + self._make_sig()
        # lowercase() normalises the prefix
        assert _verify_hmac(self._BODY, sig, self._SECRET) is True

    def test_empty_sig_returns_false(self):
        assert _verify_hmac(self._BODY, "", self._SECRET) is False


class TestCheckSignature:
    _SECRET = "mysecret"
    _BODY   = b"payload"

    def _sig(self) -> str:
        return hmac.new(self._SECRET.encode(), self._BODY, hashlib.sha256).hexdigest()

    def test_no_secret_configured_always_passes(self):
        # Must not raise even with a completely wrong/absent header
        _check_signature(self._BODY, None, "")
        _check_signature(self._BODY, "garbage", "")

    def test_valid_signature_passes(self):
        _check_signature(self._BODY, self._sig(), self._SECRET)

    # BUG-W7: secret set but header is None must raise 401
    def test_none_header_with_secret_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _check_signature(self._BODY, None, self._SECRET)
        assert exc_info.value.status_code == 401

    def test_wrong_signature_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _check_signature(self._BODY, "deadbeef", self._SECRET)
        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# Spire normaliser — BUG-W2, BUG-W5
# ---------------------------------------------------------------------------

class TestNormalizeSpire:
    _VALID = {
        "mmsi": 123456789,
        "latitude": -33.8688,
        "longitude": 151.2093,
        "speed": 7.5,
        "course": 270.0,
        "vessel_name": "MV TEST",
        "imo_number": 9876543,
        "timestamp": "2026-06-28T00:00:00Z",
    }

    def test_valid_record_normalises(self):
        result = _normalize_spire(self._VALID)
        assert result is not None
        assert result["mmsi"] == "123456789"
        assert result["lat"] == pytest.approx(-33.8688)
        assert result["lon"] == pytest.approx(151.2093)
        assert result["sog"] == pytest.approx(7.5)
        assert result["source"] == "spire"

    def test_defaults_for_optional_fields(self):
        minimal = {"mmsi": 1, "latitude": 0.0, "longitude": 0.0}
        result = _normalize_spire(minimal)
        assert result is not None
        assert result["sog"] == 0.0
        assert result["cog"] == 0.0

    # BUG-W2: out-of-range lat must return None
    def test_out_of_range_latitude_returns_none(self):
        """BUG-W2 — lat=999.0 passes the float() cast but must fail the bounds check."""
        bad = {**self._VALID, "latitude": 999.0}
        assert _normalize_spire(bad) is None

    def test_out_of_range_longitude_returns_none(self):
        bad = {**self._VALID, "longitude": -999.0}
        assert _normalize_spire(bad) is None

    def test_pole_coordinates_accepted(self):
        pole = {**self._VALID, "latitude": 90.0, "longitude": 0.0}
        assert _normalize_spire(pole) is not None

    def test_antimeridian_coordinates_accepted(self):
        anti = {**self._VALID, "longitude": 180.0}
        assert _normalize_spire(anti) is not None

    # BUG-W5: missing 'mmsi' key must return None, not propagate KeyError
    def test_missing_mmsi_returns_none(self):
        """BUG-W5 — 'mmsi' is accessed with [], so KeyError must be caught."""
        no_mmsi = {k: v for k, v in self._VALID.items() if k != "mmsi"}
        assert _normalize_spire(no_mmsi) is None

    def test_missing_latitude_returns_none(self):
        no_lat = {k: v for k, v in self._VALID.items() if k != "latitude"}
        assert _normalize_spire(no_lat) is None

    def test_non_numeric_lat_returns_none(self):
        bad = {**self._VALID, "latitude": "north"}
        assert _normalize_spire(bad) is None

    def test_mmsi_coerced_to_string(self):
        result = _normalize_spire(self._VALID)
        assert isinstance(result["mmsi"], str)

    def test_empty_record_returns_none(self):
        assert _normalize_spire({}) is None


# ---------------------------------------------------------------------------
# Orbcomm normaliser — BUG-W3, BUG-W4
# ---------------------------------------------------------------------------

class TestNormalizeOrbcomm:
    _VALID = {
        "MMSI": "987654321",
        "Position": {
            "Point": {
                "coordinates": [151.2093, -33.8688]  # [lon, lat] — GeoJSON order
            }
        },
        "SOG": 5.0,
        "COG": 90.0,
        "VesselName": "SEA GUARDIAN",
    }

    def test_valid_record_normalises(self):
        result = _normalize_orbcomm(self._VALID)
        assert result is not None
        assert result["mmsi"] == "987654321"
        assert result["source"] == "orbcomm"

    # BUG-W3: coordinates are [lon, lat]; verify the normaliser reads them correctly
    def test_lon_lat_order_is_correct(self):
        """BUG-W3 — GeoJSON coordinates are [lon, lat].  Swapping them would
        mirror every vessel position around the equator and prime meridian."""
        result = _normalize_orbcomm(self._VALID)
        assert result is not None
        assert result["lat"] == pytest.approx(-33.8688), (
            "lat extracted from coords[1], not coords[0] (GeoJSON lon-first)"
        )
        assert result["lon"] == pytest.approx(151.2093), (
            "lon extracted from coords[0]"
        )

    # BUG-W4: empty coordinates list must not crash with IndexError
    def test_empty_coordinates_returns_none(self):
        """BUG-W4 — coords=[] causes IndexError on coords[0]; must be caught."""
        bad = {**self._VALID,
               "Position": {"Point": {"coordinates": []}}}
        assert _normalize_orbcomm(bad) is None

    def test_out_of_range_lat_returns_none(self):
        bad = {**self._VALID,
               "Position": {"Point": {"coordinates": [0.0, 999.0]}}}
        assert _normalize_orbcomm(bad) is None

    def test_missing_position_returns_none(self):
        no_pos = {k: v for k, v in self._VALID.items() if k != "Position"}
        assert _normalize_orbcomm(no_pos) is None

    def test_missing_mmsi_returns_none(self):
        no_mmsi = {k: v for k, v in self._VALID.items() if k != "MMSI"}
        assert _normalize_orbcomm(no_mmsi) is None

    def test_defaults_for_optional_fields(self):
        minimal = {
            "MMSI": "1",
            "Position": {"Point": {"coordinates": [0.0, 0.0]}},
        }
        result = _normalize_orbcomm(minimal)
        assert result is not None
        assert result["sog"] == 0.0
        assert result["cog"] == 0.0


# ---------------------------------------------------------------------------
# FastAPI endpoint smoke tests (no Redis — publish_raw mocked)
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """TestClient with publish_raw patched to a no-op."""
    from fastapi.testclient import TestClient
    from vesselx.gateway.webhook import router
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    with patch("vesselx.gateway.webhook.publish_raw", new=AsyncMock(return_value=None)):
        with TestClient(app) as c:
            yield c


class TestGenericWebhookEndpoint:
    def test_accepts_valid_array(self, client):
        payload = [{"mmsi": "1", "lat": 0.0, "lon": 0.0, "sog": 0.0, "cog": 0.0}]
        r = client.post("/webhooks/generic", json=payload)
        assert r.status_code == 202
        assert r.json()["accepted"] == 1

    # BUG-W6: generic endpoint has no HMAC — document it accepts any request
    def test_no_auth_required(self, client):
        """BUG-W6 — /webhooks/generic has no signature check; any caller can inject."""
        r = client.post("/webhooks/generic",
                        json=[{"mmsi": "0", "lat": 1.0, "lon": 1.0}])
        assert r.status_code == 202

    def test_non_array_body_returns_422(self, client):
        r = client.post("/webhooks/generic", json={"mmsi": "1", "lat": 0.0})
        assert r.status_code == 422

    def test_out_of_range_lat_silently_skipped(self, client):
        payload = [{"mmsi": "1", "lat": 999.0, "lon": 0.0}]
        r = client.post("/webhooks/generic", json=payload)
        assert r.status_code == 202
        assert r.json()["accepted"] == 0

    def test_missing_lat_silently_skipped(self, client):
        payload = [{"mmsi": "1", "lon": 0.0}]
        r = client.post("/webhooks/generic", json=payload)
        assert r.status_code == 202
        assert r.json()["accepted"] == 0

    def test_mixed_valid_invalid_batch(self, client):
        payload = [
            {"mmsi": "1", "lat": 0.0, "lon": 0.0},
            {"mmsi": "2", "lat": 999.0, "lon": 0.0},  # bad
            {"mmsi": "3", "lat": -10.0, "lon": 20.0},
        ]
        r = client.post("/webhooks/generic", json=payload)
        assert r.json()["accepted"] == 2

    def test_empty_array_accepted(self, client):
        r = client.post("/webhooks/generic", json=[])
        assert r.status_code == 202
        assert r.json()["accepted"] == 0


class TestSpireWebhookEndpoint:
    _RECORD = {
        "mmsi": 123456789,
        "latitude": -10.0,
        "longitude": 20.0,
        "speed": 5.0,
        "course": 180.0,
    }

    def test_accepts_valid_batch(self, client):
        r = client.post("/webhooks/spire", json=[self._RECORD])
        assert r.status_code == 202
        assert r.json()["accepted"] == 1

    def test_invalid_record_in_batch_skipped(self, client):
        batch = [self._RECORD, {"latitude": "bad", "longitude": 0.0}]
        r = client.post("/webhooks/spire", json=batch)
        assert r.status_code == 202
        assert r.json()["accepted"] == 1

    def test_non_list_body_returns_422(self, client):
        r = client.post("/webhooks/spire", json={"mmsi": 1})
        assert r.status_code == 422


class TestOrbcommWebhookEndpoint:
    _RECORD = {
        "MMSI": "987654321",
        "Position": {"Point": {"coordinates": [20.0, -10.0]}},
        "SOG": 3.0,
        "COG": 45.0,
    }

    def test_accepts_valid_batch(self, client):
        r = client.post("/webhooks/orbcomm", json=[self._RECORD])
        assert r.status_code == 202
        assert r.json()["accepted"] == 1

    def test_empty_coords_skipped_not_crashed(self, client):
        bad = {**self._RECORD,
               "Position": {"Point": {"coordinates": []}}}
        r = client.post("/webhooks/orbcomm", json=[bad])
        assert r.status_code == 202
        assert r.json()["accepted"] == 0
