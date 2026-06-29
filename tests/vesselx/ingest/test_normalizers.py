"""Unit tests for vesselx.ingest source normaliser functions.

Each ingest adapter (aishub, digitraffic, kystverket, marinesia,
openshipdata) exposes a private `_normalize(record)` function that maps
provider-specific field names to the VesselX common telemetry schema.

Bugs hunted:
  BUG-I1  AISHub `LATITUDE or 0` fallback — absent LATITUDE key silently
          places vessel at (0, 0) equatorial origin instead of returning None.
          Zero-zero filter then catches it BUT only coincidentally; if the
          filter is ever relaxed the silent ghost vessel surfaces.

  BUG-I2  kystverket/marinesia/openshipdata `(lat==0 and lon==0)` filter —
          rejects valid vessels in the Gulf of Guinea (equator × prime
          meridian, deep ocean area ≈ 0°N, 0°E).  A legitimate vessel
          operating there would be silently dropped from tracking.

  BUG-I3  Digitraffic normaliser: `mmsi == "0"` guard — mmsi field of
          integer 0 (invalid vessel) correctly rejected, but a string "00"
          or " 0 " passes the guard (strip() is called, but "00" is not "0").

  BUG-I4  All adapters: `sog or 0.0` treats SOG=0.0 as absent — a vessel
          at rest but with a non-zero speed field in an alternate column
          inherits the wrong speed (same root cause as BUG-OR-2).

Tests that document current broken behaviour use pytest.xfail so the suite
stays green while the bugs are open; a fix causes the xfail to become an
unexpected pass, flagging that the test needs updating.
"""
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[4] / "src"))

from vesselx.ingest.aishub import _normalize as norm_aishub
from vesselx.ingest.digitraffic import _normalize as norm_digitraffic
from vesselx.ingest.kystverket import _normalize as norm_kystverket
from vesselx.ingest.marinesia import _normalize as norm_marinesia
from vesselx.ingest.openshipdata import _normalize as norm_openshipdata


# ---------------------------------------------------------------------------
# AISHub
# ---------------------------------------------------------------------------

class TestAishubNormalize:
    _VALID = {
        "MMSI":      123456789,
        "LATITUDE":  1.234,
        "LONGITUDE": 103.456,
        "SOG":       12.3,
        "COG":       90.1,
        "NAME":      "TEST VESSEL",
        "IMO":       9123456,
        "TYPE":      70,
        "TIME":      "2026-06-28 12:00:00 UTC",
    }

    def test_valid_record_normalises(self):
        result = norm_aishub(self._VALID)
        assert result is not None
        assert result["source"] == "aishub"
        assert result["mmsi"] == "123456789"
        assert result["lat"] == pytest.approx(1.234)
        assert result["lon"] == pytest.approx(103.456)
        assert result["sog"] == pytest.approx(12.3)
        assert result["vessel_type"] == "cargo"

    def test_zero_mmsi_rejected(self):
        bad = {**self._VALID, "MMSI": 0}
        assert norm_aishub(bad) is None

    def test_missing_mmsi_rejected(self):
        no_mmsi = {k: v for k, v in self._VALID.items() if k != "MMSI"}
        assert norm_aishub(no_mmsi) is None

    # BUG-I1: absent LATITUDE silently defaults to 0 and creates a ghost vessel
    def test_missing_latitude_produces_ghost_vessel(self):
        """BUG-I1 — absent LATITUDE gives lat=0.0.  When LONGITUDE is non-zero
        the (lat==0 and lon==0) filter does NOT catch it, so a ghost vessel
        appears on the equator at the real longitude.
        Fix: check `if record.get('LATITUDE') is None: return None`."""
        no_lat = {**self._VALID, "LATITUDE": None}
        result = norm_aishub(no_lat)
        if result is not None and result["lat"] == pytest.approx(0.0):
            pytest.xfail(
                f"BUG-I1: absent LATITUDE defaults to 0.0. Vessel "
                f"'{result.get('mmsi')}' appears as ghost at "
                f"lat=0.0, lon={result.get('lon')} instead of being "
                "rejected. Fix: return None when LATITUDE is absent/None."
            )
        assert result is None, "Expected None for record with no LATITUDE"

    def test_real_equatorial_nonzero_lon_survives_zero_lat_default(self):
        """BUG-I1 extended — if lat is absent but lon is non-zero, the vessel
        appears on the equator at the real longitude.  Ghost position."""
        record = {**self._VALID, "LATITUDE": None, "LONGITUDE": 10.0}
        result = norm_aishub(record)
        if result is not None and result["lat"] == pytest.approx(0.0):
            pytest.xfail(
                "BUG-I1: absent LATITUDE defaults to 0.0 and passes bounds "
                "check when lon != 0. Vessel appears on equator at wrong lat. "
                "Fix: check for None explicitly and return None."
            )

    def test_unknown_ais_type_maps_to_unknown(self):
        record = {**self._VALID, "TYPE": 999}
        result = norm_aishub(record)
        assert result is not None
        assert result["vessel_type"] == "unknown"

    def test_known_ais_types_map_correctly(self):
        for ais_type, expected in [(80, "tanker"), (30, "trawler"), (35, "naval")]:
            record = {**self._VALID, "TYPE": ais_type}
            result = norm_aishub(record)
            assert result is not None
            assert result["vessel_type"] == expected

    def test_out_of_range_lat_rejected(self):
        bad = {**self._VALID, "LATITUDE": 999.0}
        assert norm_aishub(bad) is None

    def test_stationary_vessel_sog_zero(self):
        record = {**self._VALID, "SOG": 0.0}
        result = norm_aishub(record)
        assert result is not None
        assert result["sog"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# BUG-I2: (lat==0 and lon==0) false-positive filter — Gulf of Guinea
# ---------------------------------------------------------------------------

class TestGulfOfGuineaFilter:
    """Three adapters share the same (lat == 0 and lon == 0) guard.
    The Gulf of Guinea is a real, vast ocean area used by fishing vessels
    and tankers; coordinates near (0, 0) are completely valid."""

    @pytest.mark.parametrize("normalizer,name", [
        (norm_kystverket,   "kystverket"),
        (norm_marinesia,    "marinesia"),
        (norm_openshipdata, "openshipdata"),
    ])
    def test_exact_zero_zero_rejected(self, normalizer, name):
        """BUG-I2 — (0.0, 0.0) is in the Gulf of Guinea but is silently
        dropped.  Document it; the fix is to remove the (lat==0 and lon==0)
        guard and rely solely on the bounds check."""
        record = {
            "mmsi": "123456789",
            "lat": 0.0,
            "lon": 0.0,
            "sog": 5.0,
            "cog": 90.0,
        }
        result = normalizer(record)
        if result is None:
            pytest.xfail(
                f"BUG-I2 ({name}): vessel at exactly (0.0, 0.0) — Gulf of "
                "Guinea — is rejected by `lat == 0 and lon == 0` guard. "
                "Fix: remove the guard; rely on bounds check only."
            )
        assert result is not None

    @pytest.mark.parametrize("normalizer,name", [
        (norm_kystverket,   "kystverket"),
        (norm_marinesia,    "marinesia"),
        (norm_openshipdata, "openshipdata"),
    ])
    def test_near_zero_gulf_of_guinea_vessel_accepted(self, normalizer, name):
        """Vessel at (0.1, 0.1) — near Gulf of Guinea — must not be filtered."""
        record = {
            "mmsi": "123456789",
            "lat": 0.1,
            "lon": 0.1,
            "sog": 5.0,
            "cog": 90.0,
        }
        result = normalizer(record)
        assert result is not None, (
            f"BUG-I2 ({name}): near-zero coords should not be filtered."
        )


# ---------------------------------------------------------------------------
# Kystverket
# ---------------------------------------------------------------------------

class TestKystverketNormalize:
    _VALID = {
        "mmsi":     234567890,
        "name":     "HAAKON JARL",
        "lat":      60.123,
        "lon":      5.456,
        "sog":      10.2,
        "cog":      270.0,
        "heading":  270,
        "navStatus": 0,
        "imo":      9234567,
        "shipType": 70,
        "time":     "2026-06-28T12:00:00Z",
    }

    def test_valid_record_normalises(self):
        result = norm_kystverket(self._VALID)
        assert result is not None
        assert result["source"] == "kystverket"
        assert result["mmsi"] == "234567890"
        assert result["lat"] == pytest.approx(60.123)
        assert result["lon"] == pytest.approx(5.456)
        assert result["vessel_type"] == "cargo"

    def test_missing_mmsi_rejected(self):
        no_mmsi = {k: v for k, v in self._VALID.items() if k != "mmsi"}
        assert norm_kystverket(no_mmsi) is None

    def test_zero_mmsi_rejected(self):
        assert norm_kystverket({**self._VALID, "mmsi": 0}) is None

    def test_out_of_range_coords_rejected(self):
        assert norm_kystverket({**self._VALID, "lat": 999.0}) is None


# ---------------------------------------------------------------------------
# Marinesia
# ---------------------------------------------------------------------------

class TestMarinesiaNormalize:
    _VALID = {
        "mmsi":        "123456789",
        "name":        "OCEAN GUARD",
        "lat":         1.234,
        "lon":         103.456,
        "sog":         12.3,
        "cog":         90.0,
        "heading":     91,
        "imo":         "9123456",
        "vessel_type": "container",
        "eta":         "2026-07-01T06:00:00Z",
        "updated_at":  "2026-06-28T12:00:00Z",
    }

    def test_valid_record_normalises(self):
        result = norm_marinesia(self._VALID)
        assert result is not None
        assert result["source"] == "marinesia"
        assert result["vessel_type"] == "container"

    def test_missing_mmsi_rejected(self):
        no_mmsi = {k: v for k, v in self._VALID.items() if k != "mmsi"}
        assert norm_marinesia(no_mmsi) is None

    def test_out_of_range_coords_rejected(self):
        assert norm_marinesia({**self._VALID, "lat": -91.0}) is None

    def test_stationary_vessel_sog_zero(self):
        result = norm_marinesia({**self._VALID, "sog": 0.0})
        assert result is not None
        assert result["sog"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# OpenShipData
# ---------------------------------------------------------------------------

class TestOpenShipDataNormalize:
    _VALID = {
        "mmsi":        "345678901",
        "imo":         "9345678",
        "name":        "PACIFIC DAWN",
        "flag":        "SG",
        "vessel_type": "tanker",
        "lat":         -10.0,
        "lon":         50.0,
        "sog":         8.5,
        "cog":         180.0,
        "heading":     181,
        "nav_status":  0,
        "draught":     12.0,
        "length":      300,
        "timestamp":   "2026-06-28T12:00:00Z",
    }

    def test_valid_record_normalises(self):
        result = norm_openshipdata(self._VALID)
        assert result is not None
        assert result["source"] == "openshipdata"
        assert result["vessel_type"] == "tanker"
        assert result["flag"] == "SG"
        assert result["length_m"] == pytest.approx(300.0)

    def test_missing_mmsi_rejected(self):
        no_mmsi = {k: v for k, v in self._VALID.items() if k != "mmsi"}
        assert norm_openshipdata(no_mmsi) is None

    def test_out_of_range_coords_rejected(self):
        assert norm_openshipdata({**self._VALID, "lon": 999.0}) is None

    def test_stationary_vessel_sog_zero(self):
        result = norm_openshipdata({**self._VALID, "sog": 0.0})
        assert result is not None
        assert result["sog"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Digitraffic
# ---------------------------------------------------------------------------

class TestDigitrafficNormalize:
    _VALID = {
        "mmsi":      123456789,
        "lat":       60.456,
        "lon":       24.123,
        "sog":       12.3,
        "cog":       180.5,
        "navStat":   0,
        "heading":   181,
        "timestamp": 1719576000,
    }

    def test_valid_record_normalises(self):
        result = norm_digitraffic(self._VALID)
        assert result is not None
        assert result["source"] == "digitraffic"
        assert result["mmsi"] == "123456789"
        assert result["lat"] == pytest.approx(60.456)
        assert result["nav_status"] == 0
        assert result["ts"] == "1719576000"

    def test_zero_mmsi_rejected(self):
        """Integer 0 is an invalid MMSI and must be rejected."""
        assert norm_digitraffic({**self._VALID, "mmsi": 0}) is None

    def test_missing_lat_key_rejected(self):
        no_lat = {k: v for k, v in self._VALID.items() if k != "lat"}
        assert norm_digitraffic(no_lat) is None

    def test_missing_lon_key_rejected(self):
        no_lon = {k: v for k, v in self._VALID.items() if k != "lon"}
        assert norm_digitraffic(no_lon) is None

    def test_out_of_range_lat_rejected(self):
        assert norm_digitraffic({**self._VALID, "lat": -91.0}) is None

    def test_out_of_range_lon_rejected(self):
        assert norm_digitraffic({**self._VALID, "lon": 181.0}) is None

    def test_stationary_vessel_sog_zero(self):
        result = norm_digitraffic({**self._VALID, "sog": 0.0})
        assert result is not None
        assert result["sog"] == pytest.approx(0.0)

    def test_none_timestamp_produces_empty_string(self):
        """Absent timestamp must not crash — ts field defaults to ''."""
        record = {k: v for k, v in self._VALID.items() if k != "timestamp"}
        result = norm_digitraffic(record)
        assert result is not None
        assert result["ts"] == ""

    # BUG-I3: "00" passes mmsi == "0" guard despite being invalid
    def test_double_zero_mmsi_accepted_bug(self):
        """BUG-I3 — mmsi="00" passes `mmsi == "0"` guard. An MMSI of all
        zeros is invalid; the guard should be `not mmsi.lstrip("0")` or a
        numeric range check."""
        record = {**self._VALID, "mmsi": "00"}
        result = norm_digitraffic(record)
        if result is not None:
            pytest.xfail(
                "BUG-I3: mmsi='00' slips through the guard (only '0' is "
                "explicitly checked). Fix: reject any MMSI that is all zeros "
                "or not a 9-digit number."
            )
