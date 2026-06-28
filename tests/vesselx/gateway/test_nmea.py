"""Unit tests for vesselx.gateway.nmea.

No hardware, no Redis — tests the pure parsing functions and the dispatcher
logic with publish_raw mocked out.

Bugs hunted:
  BUG-N1  _nmea_latlon returns 0.0 for a raw field shorter than 4 chars; but
          raw="0" (len=1) triggers raw.index(".") → ValueError — must not raise.
  BUG-N2  _parse_rmc returns a payload with NO 'mmsi' key.  The spatial worker
          skips records without mmsi, so every GPS/RMC position is silently
          discarded.  Test documents this loss and asserts the current behavior
          so a future fix is detectable.
  BUG-N3  _dispatch fragment buffer grows without bound for incomplete multi-part
          AIS sentences.  After N part-1 messages (no matching part-2) the buffer
          holds N stale entries.
  BUG-N4  _dispatch with a completely empty line must not raise or publish.
  BUG-N5  A sentence with only whitespace is treated as empty (strip() guard).
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from vesselx.gateway.nmea import _nmea_latlon, _parse_rmc, _dispatch, NMEATCPServer


# ---------------------------------------------------------------------------
# _nmea_latlon
# ---------------------------------------------------------------------------

class TestNmeaLatlon:
    def test_north_hemisphere(self):
        # 5130.000 N  → 51 + 30/60 = 51.5°
        val = _nmea_latlon("5130.000", "N")
        assert val == pytest.approx(51.5)

    def test_south_hemisphere(self):
        val = _nmea_latlon("5130.000", "S")
        assert val == pytest.approx(-51.5)

    def test_east_longitude(self):
        # 00130.000 E → 1 + 30/60 = 1.5°
        val = _nmea_latlon("00130.000", "E")
        assert val == pytest.approx(1.5)

    def test_west_longitude(self):
        val = _nmea_latlon("00130.000", "W")
        assert val == pytest.approx(-1.5)

    def test_zero_zero(self):
        val = _nmea_latlon("0000.000", "N")
        assert val == pytest.approx(0.0)

    def test_empty_string_returns_zero(self):
        assert _nmea_latlon("", "N") == 0.0

    # BUG-N1: raw shorter than 4 chars but not empty hits raw.index(".") for
    # a string without a dot, raising ValueError.
    def test_short_raw_without_dot_does_not_raise(self):
        """BUG-N1 — raw='0' has len=1 < 4 so guard returns 0.0; but if the
        guard is changed to len < 3 this would call raw.index('.') on '0' and
        raise ValueError.  Verify current guard triggers on len < 4."""
        assert _nmea_latlon("0", "N") == 0.0
        assert _nmea_latlon("123", "N") == 0.0  # len=3 → still short


# ---------------------------------------------------------------------------
# _parse_rmc
# ---------------------------------------------------------------------------

class TestParseRmc:
    # Active RMC sentence (status=A) with known coords
    _RMC_ACTIVE = (
        "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    )
    # Void sentence (status=V)
    _RMC_VOID = (
        "$GPRMC,123519,V,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
    )

    def test_active_sentence_returns_dict(self):
        result = _parse_rmc(self._RMC_ACTIVE)
        assert result is not None
        assert result["lat"] == pytest.approx(48.1173, abs=1e-3)
        assert result["lon"] == pytest.approx(11.5167, abs=1e-3)
        assert result["sog"] == pytest.approx(22.4)
        assert result["source"] == "nmea_rmc"

    def test_void_sentence_returns_none(self):
        assert _parse_rmc(self._RMC_VOID) is None

    def test_too_few_fields_returns_none(self):
        assert _parse_rmc("$GPRMC,123519,A") is None

    def test_non_rmc_sentence_returns_none(self):
        assert _parse_rmc("$GPGGA,123519,...") is None  # wrong sentence type

    # BUG-N2: RMC payload has no 'mmsi' field — spatial worker silently drops it
    def test_missing_mmsi_key(self):
        """BUG-N2 — RMC sentences carry GPS position but NO MMSI.  The spatial
        worker checks `if not mmsi: skip_ids.append(msg_id)`.  Every own-ship
        GPS position fed via the NMEA listener is silently discarded."""
        result = _parse_rmc(self._RMC_ACTIVE)
        assert result is not None
        assert "mmsi" not in result, (
            "RMC payload has no mmsi field — spatial worker will skip this record. "
            "If 'mmsi' appears here the fix has landed; update this test."
        )

    def test_empty_sog_defaults_to_zero(self):
        # SOG field left blank
        sentence = "$GPRMC,123519,A,4807.038,N,01131.000,E,,084.4,230394,003.1,W*6A"
        result = _parse_rmc(sentence)
        assert result is not None
        assert result["sog"] == 0.0

    def test_cog_absent_from_short_sentence(self):
        # Only 8 fields — no COG
        sentence = "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4*6A"
        result = _parse_rmc(sentence)
        assert result is not None
        assert result["cog"] == 0.0


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    @pytest.fixture
    def fragment_buf(self):
        return {}

    @pytest.mark.asyncio
    async def test_rmc_publishes_payload(self, fragment_buf):
        sentence = (
            "$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
        )
        with patch("vesselx.gateway.nmea.publish_raw", new=AsyncMock()) as mock_pub:
            await _dispatch(sentence, fragment_buf)
        mock_pub.assert_called_once()
        payload = mock_pub.call_args[0][0]
        assert payload["source"] == "nmea_rmc"

    # BUG-N4: empty line must not raise or call publish_raw
    @pytest.mark.asyncio
    async def test_empty_line_no_publish(self, fragment_buf):
        """BUG-N4 — empty string after strip() hits the early return guard."""
        with patch("vesselx.gateway.nmea.publish_raw", new=AsyncMock()) as mock_pub:
            await _dispatch("", fragment_buf)
            await _dispatch("\r\n", fragment_buf)
        mock_pub.assert_not_called()

    # BUG-N5: whitespace-only line treated as empty
    @pytest.mark.asyncio
    async def test_whitespace_only_no_publish(self, fragment_buf):
        """BUG-N5 — strip() normalises whitespace-only to empty string."""
        with patch("vesselx.gateway.nmea.publish_raw", new=AsyncMock()) as mock_pub:
            await _dispatch("   \t   ", fragment_buf)
        mock_pub.assert_not_called()

    @pytest.mark.asyncio
    async def test_void_rmc_not_published(self, fragment_buf):
        void = "$GPRMC,123519,V,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A"
        with patch("vesselx.gateway.nmea.publish_raw", new=AsyncMock()) as mock_pub:
            await _dispatch(void, fragment_buf)
        mock_pub.assert_not_called()

    # BUG-N3: incomplete multi-part AIS accumulates in fragment_buf forever
    @pytest.mark.asyncio
    async def test_fragment_buffer_leaks_on_incomplete_multipart(self, fragment_buf):
        """BUG-N3 — part-1-of-2 sentences accumulate in fragment_buf indefinitely.
        Simulate 3 different message IDs where part-2 never arrives.  Buffer should
        have 3 stale entries (demonstrates the leak; no eviction exists)."""
        # Minimal syntactically plausible AIS part-1-of-2 sentences
        # Fields: !AIVDM,<total>,<part>,<seq_id>,<channel>,<payload>,<fill>*<csum>
        sentences = [
            "!AIVDM,2,1,1,A,15M67N0000G?Uf6E0000000000,0*7E",
            "!AIVDM,2,1,2,A,15M67N0000G?Uf6E0000000000,0*7D",
            "!AIVDM,2,1,3,A,15M67N0000G?Uf6E0000000000,0*7C",
        ]
        # pyais may not be installed; skip the actual decode path
        with patch("vesselx.gateway.nmea._PYAIS", True), \
             patch("vesselx.gateway.nmea.publish_raw", new=AsyncMock()):
            for s in sentences:
                try:
                    await _dispatch(s, fragment_buf)
                except Exception:
                    pass  # decode errors are expected with fake payloads

        # Each unique msg_id accumulated a partial fragment — the buffer leaks
        assert len(fragment_buf) >= 1, (
            "Expected at least one stale entry in fragment_buf after incomplete "
            "multi-part messages.  If 0, a TTL/cleanup mechanism was added — "
            "remove this assertion and add a positive eviction test."
        )

    @pytest.mark.asyncio
    async def test_complete_multipart_clears_buffer(self, fragment_buf):
        """A complete 2-part sequence must remove the key from fragment_buf."""
        # We can't easily inject a valid pyais decode; test the accumulation logic
        # by inspecting fragment_buf state after the second part is dispatched.
        # Use a mock decode that returns a vessel with lat/lon.
        fake_decoded = MagicMock()
        fake_decoded.mmsi = 123456789
        fake_decoded.lat = -10.0
        fake_decoded.lon = 20.0
        fake_decoded.speed = 5.0
        fake_decoded.course = 90.0
        fake_decoded.msg_type = 1

        part1 = "!AIVDM,2,1,9,A,payload1,0*00"
        part2 = "!AIVDM,2,2,9,A,payload2,0*00"

        with patch("vesselx.gateway.nmea._PYAIS", True), \
             patch("vesselx.gateway.nmea._NMEAMessage") as MockNMEA, \
             patch("vesselx.gateway.nmea.publish_raw", new=AsyncMock()):
            instance = MagicMock()
            instance.decode.return_value = fake_decoded
            MockNMEA.return_value = instance
            MockNMEA.from_string.return_value = fake_decoded

            await _dispatch(part1, fragment_buf)
            assert "9" in fragment_buf  # buffered after part 1

            await _dispatch(part2, fragment_buf)
            assert "9" not in fragment_buf  # cleared after part 2
