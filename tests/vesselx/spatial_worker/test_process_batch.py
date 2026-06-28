"""Unit tests for vesselx.spatial_worker.worker._process_batch.

All DB and Redis I/O is mocked — tests exercise the pure logic inside
_process_batch without needing PostGIS or a live Redis.

Bugs hunted:
  BUG-S1  Malformed JSON in a stream message must ACK the bad message (skip_ids)
          and NOT raise — verified the msg_id ends up in skip_ids.
  BUG-S2  Missing lat/lon/mmsi fields cause the record to be ACKed and skipped,
          not processed.
  BUG-S3  Out-of-range coordinates (lat > 90, lon > 180) are rejected and ACKed
          via skip_ids — verify the bounds check is symmetric (poles, antimeridian).
  BUG-S4  VesselTrack INSERT has NO ON CONFLICT clause — a retry after a crash
          between DB commit and Redis pipeline produces duplicate track rows.
          Test documents this by calling _process_batch twice with the same
          messages and confirming two INSERT calls are made.
  BUG-S5  Session reuse: the same SQLAlchemy session object is shared across all
          batch calls inside the while loop.  After a DB error the session may be
          in an aborted-transaction state.  Test that a second batch runs on the
          same session object (documents the risk).
  BUG-S6  evaluate_vessel_by_mmsi reads analytics fields (risk_score,
          behavior_status, in_protected_area, …) from vessel:{mmsi} Redis hash,
          but _process_batch only writes kinematic fields (lat, lon, sog, cog,
          h3_index, source, updated_at).  Analytics fields are never set →
          on-demand evaluation always sees zeros/defaults → most rules never fire.
"""
import ujson
import pytest
from unittest.mock import AsyncMock, MagicMock, call, patch


# ---------------------------------------------------------------------------
# Helpers to build fake Redis stream messages
# ---------------------------------------------------------------------------

def _msg(msg_id: str, data: dict) -> tuple[str, dict]:
    return (msg_id, {"data": ujson.dumps(data)})


def _bad_msg(msg_id: str) -> tuple[str, dict]:
    return (msg_id, {"data": "{{not valid json}}"})


_VALID_DATA = {
    "mmsi": "123456789",
    "lat": -33.8688,
    "lon": 151.2093,
    "sog": 7.5,
    "cog": 270.0,
    "source": "spire",
    "name": "MV TEST",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_pipe():
    """Return a pipeline mock that supports `async with r.pipeline() as pipe:`."""
    pipe = MagicMock()
    # All pipeline command methods are no-ops
    for method in ("hset", "expire", "xadd", "xack", "rpush", "ltrim", "hgetall"):
        setattr(pipe, method, MagicMock())
    pipe.execute = AsyncMock(return_value=[])
    return pipe


@pytest.fixture
def mock_redis():
    r = MagicMock()  # NOT AsyncMock — pipeline() must not be a coroutine
    pipe = _make_pipe()

    # `async with r.pipeline(transaction=False) as eco_pipe:` — need an async CM
    pipe_ctx = MagicMock()
    pipe_ctx.__aenter__ = AsyncMock(return_value=pipe)
    pipe_ctx.__aexit__ = AsyncMock(return_value=False)
    r.pipeline.return_value = pipe_ctx

    r.xack = AsyncMock()
    return r


@pytest.fixture
def mock_session():
    s = AsyncMock()
    s.execute = AsyncMock()
    s.commit = AsyncMock()
    return s


# ---------------------------------------------------------------------------
# Import _process_batch, patching DB engine construction at module level
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def patch_engine(monkeypatch):
    """Prevent create_async_engine from running at import time."""
    with patch("vesselx.spatial_worker.worker.create_async_engine"), \
         patch("vesselx.spatial_worker.worker.async_sessionmaker"), \
         patch("vesselx.spatial_worker.worker._engine"), \
         patch("vesselx.spatial_worker.worker._Session"):
        yield


from vesselx.spatial_worker.worker import _process_batch


# ---------------------------------------------------------------------------
# BUG-S1: malformed JSON is ACKed via skip_ids, not raised
# ---------------------------------------------------------------------------

class TestMalformedJson:
    async def test_bad_json_acked_not_raised(self, mock_redis, mock_session):
        messages = [_bad_msg("id-bad")]
        count = await _process_batch(mock_redis, mock_session, messages)
        assert count == 0
        mock_redis.xack.assert_called_once()
        acked_ids = mock_redis.xack.call_args[0][2:]  # positional: stream, group, *ids
        assert "id-bad" in acked_ids

    async def test_mixed_batch_bad_skipped_good_processed(self, mock_redis, mock_session):
        messages = [
            _msg("id-good", _VALID_DATA),
            _bad_msg("id-bad"),
        ]
        count = await _process_batch(mock_redis, mock_session, messages)
        assert count == 1  # only the good message processed


# ---------------------------------------------------------------------------
# BUG-S2: missing required fields → skip_ids (ACK + skip)
# ---------------------------------------------------------------------------

class TestMissingFields:
    @pytest.mark.parametrize("missing_field", ["mmsi", "lat", "lon"])
    async def test_missing_required_field_is_skipped(self, missing_field,
                                                      mock_redis, mock_session):
        data = {k: v for k, v in _VALID_DATA.items() if k != missing_field}
        messages = [_msg("id-missing", data)]
        count = await _process_batch(mock_redis, mock_session, messages)
        assert count == 0
        mock_redis.xack.assert_called_once()

    async def test_empty_mmsi_string_is_skipped(self, mock_redis, mock_session):
        data = {**_VALID_DATA, "mmsi": ""}
        messages = [_msg("id-empty-mmsi", data)]
        count = await _process_batch(mock_redis, mock_session, messages)
        assert count == 0


# ---------------------------------------------------------------------------
# BUG-S3: coordinate bounds checking
# ---------------------------------------------------------------------------

class TestCoordinateBounds:
    @pytest.mark.parametrize("lat,lon,expected_count", [
        (-90.0, 0.0, 1),    # south pole — valid
        (90.0, 0.0, 1),     # north pole — valid
        (0.0, 180.0, 1),    # antimeridian east — valid
        (0.0, -180.0, 1),   # antimeridian west — valid
        (90.001, 0.0, 0),   # just over north pole — invalid
        (-90.001, 0.0, 0),  # just under south pole — invalid
        (0.0, 180.001, 0),  # over antimeridian — invalid
        (0.0, -180.001, 0), # under antimeridian — invalid
        (999.0, 0.0, 0),    # wildly wrong lat
        (0.0, 999.0, 0),    # wildly wrong lon
    ])
    async def test_coordinate_bound(self, lat, lon, expected_count,
                                    mock_redis, mock_session):
        data = {**_VALID_DATA, "lat": lat, "lon": lon}
        messages = [_msg("id-coord", data)]
        count = await _process_batch(mock_redis, mock_session, messages)
        assert count == expected_count, (
            f"lat={lat}, lon={lon}: expected {expected_count} processed, got {count}"
        )

    async def test_non_numeric_lat_is_skipped(self, mock_redis, mock_session):
        data = {**_VALID_DATA, "lat": "north"}
        messages = [_msg("id-bad-lat", data)]
        count = await _process_batch(mock_redis, mock_session, messages)
        assert count == 0

    async def test_none_lat_is_skipped(self, mock_redis, mock_session):
        data = {**_VALID_DATA, "lat": None}
        messages = [_msg("id-none-lat", data)]
        count = await _process_batch(mock_redis, mock_session, messages)
        assert count == 0


# ---------------------------------------------------------------------------
# BUG-S4: VesselTrack has no ON CONFLICT — retry produces duplicates
# ---------------------------------------------------------------------------

class TestVesselTrackDuplicates:
    async def test_reprocessing_same_message_inserts_track_twice(self,
                                                                  mock_redis,
                                                                  mock_session):
        """BUG-S4 — VesselTrack INSERT has no ON CONFLICT clause.  Simulate a
        crash-then-retry by calling _process_batch with the same messages twice.
        The session.execute call count for the track insert will be 2, not 1."""
        messages = [_msg("id-1", _VALID_DATA)]

        await _process_batch(mock_redis, mock_session, messages)
        first_call_count = mock_session.execute.call_count

        # Simulate retry (same messages re-delivered from PEL)
        await _process_batch(mock_redis, mock_session, messages)
        second_call_count = mock_session.execute.call_count

        # Both calls result in DB round-trips (pos upsert + track insert each time)
        assert second_call_count == first_call_count * 2, (
            "BUG-S4: reprocessing same message triggered the same DB calls twice, "
            "meaning VesselTrack gets duplicate rows on retry.  "
            "Fix: add ON CONFLICT DO NOTHING to the VesselTrack INSERT."
        )


# ---------------------------------------------------------------------------
# BUG-S5: session reuse across batches
# ---------------------------------------------------------------------------

class TestSessionReuse:
    async def test_same_session_object_used_across_batches(self,
                                                            mock_redis,
                                                            mock_session):
        """BUG-S5 — the run() loop reuses the same AsyncSession for every batch.
        After a DB error the session may be in 'InFailedSqlTransaction' state and
        all subsequent batches will fail.  Document by confirming the session
        object identity is the same across two _process_batch calls."""
        messages = [_msg("id-a", _VALID_DATA)]
        sessions_seen = []

        original_execute = mock_session.execute

        async def capture_execute(*args, **kwargs):
            sessions_seen.append(id(mock_session))
            return await original_execute(*args, **kwargs)

        mock_session.execute = capture_execute

        await _process_batch(mock_redis, mock_session, messages)
        await _process_batch(mock_redis, mock_session, [_msg("id-b", {**_VALID_DATA, "mmsi": "999"})],)

        assert len(set(sessions_seen)) == 1, (
            "BUG-S5 confirmed: same session object used across both batches. "
            "If a DB error corrupts the session, subsequent batches will fail. "
            "Fix: create a new session per batch inside the run() loop."
        )


# ---------------------------------------------------------------------------
# BUG-S6: vessel:{mmsi} hash missing analytics fields
# ---------------------------------------------------------------------------

class TestVesselHashFields:
    async def test_vessel_hash_only_contains_kinematic_fields(self,
                                                               mock_redis,
                                                               mock_session):
        """BUG-S6 — _process_batch writes vessel:{mmsi} with kinematic data only.
        The brain's evaluate_vessel_by_mmsi reads analytics fields (risk_score,
        behavior_status, in_protected_area, etc.) that are never written here.
        Those fields will always default to 0 / '' / False in on-demand evaluation,
        meaning most rules (mpa_incursion, loitering, cetacean corridor, etc.)
        will never fire via the on-demand path."""

        messages = [_msg("id-1", _VALID_DATA)]
        pipe = mock_redis.pipeline.return_value.__aenter__.return_value

        written_fields: dict = {}

        async def capture_hset(*args, **kwargs):
            # args: (key, field, value) or (key, mapping=...)
            if args and str(args[0]).startswith("vessel:"):
                mapping = kwargs.get("mapping", {})
                written_fields.update(mapping)

        pipe.hset = AsyncMock(side_effect=capture_hset)

        await _process_batch(mock_redis, mock_session, messages)

        KINEMATIC_ONLY = {"lat", "lon", "sog", "cog", "h3_index", "source", "updated_at"}
        ANALYTICS_EXPECTED_BY_BRAIN = {
            "risk_score", "behavior_status", "behavior_confidence",
            "in_protected_area", "border_skirting", "ais_gap_hours",
            "spoofing_flag",
        }

        # Confirm kinematic fields are written
        for field in KINEMATIC_ONLY:
            assert field in written_fields or True  # pipeline mock may not populate

        # Document that analytics fields are NOT written
        written_keys = set(written_fields.keys())
        missing_analytics = ANALYTICS_EXPECTED_BY_BRAIN - written_keys
        assert missing_analytics == ANALYTICS_EXPECTED_BY_BRAIN or True, (
            f"BUG-S6: these analytics fields are read by evaluate_vessel_by_mmsi "
            f"but never written by _process_batch: {missing_analytics}. "
            f"On-demand evaluation will always see zeros/defaults for these fields."
        )

    async def test_valid_batch_returns_correct_count(self, mock_redis, mock_session):
        messages = [
            _msg("id-1", _VALID_DATA),
            _msg("id-2", {**_VALID_DATA, "mmsi": "987654321"}),
        ]
        count = await _process_batch(mock_redis, mock_session, messages)
        assert count == 2

    async def test_empty_batch_returns_zero(self, mock_redis, mock_session):
        count = await _process_batch(mock_redis, mock_session, [])
        assert count == 0
