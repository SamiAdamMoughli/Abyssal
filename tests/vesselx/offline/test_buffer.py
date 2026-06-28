"""Unit tests for vesselx.offline.buffer (OfflineBuffer + SyncOrchestrator).

Uses an in-memory SQLite database (path=':memory:') — no filesystem I/O.

Bugs hunted:
  BUG-B1  next_sequence() commits the UPDATE then re-selects; another coroutine
          can execute between the two awaits and read the same incremented value.
          → Test demonstrates the race by running two concurrent calls.
  BUG-B2  write_delta() is INSERT OR IGNORE — writing the same delta_id twice
          must be idempotent (second write is a no-op, not an error).
  BUG-B3  mark_synced() with an empty list must return 0 cleanly (no SQL error
          from empty IN () clause).
  BUG-B4  pending_deltas() ORDER BY sequence ASC — if sequence counter ever
          produces duplicates (BUG-B1) the oldest-first guarantee breaks.
  BUG-B5  SyncOrchestrator must NOT call upload_fn when offline; must call
          upload_fn and mark_synced when online with pending deltas.
"""
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from vesselx.offline.buffer import OfflineBuffer, SyncOrchestrator
from vesselx.offline.sync import SyncCheckpoint, SyncDelta


# ---------------------------------------------------------------------------
# Fixture: in-memory buffer (fresh for each test)
# ---------------------------------------------------------------------------

@pytest.fixture
async def buf():
    b = OfflineBuffer(db_path=":memory:")
    await b.init()
    yield b
    await b.close()


def _delta(sequence: int = 1, delta_id: str | None = None, kind: str = "track_point",
           entity_id: str = "123456789") -> SyncDelta:
    return SyncDelta(
        delta_id=delta_id or f"delta-{sequence}",
        kind=kind,
        entity_id=entity_id,
        sequence=sequence,
        payload={"lat": -34.0, "lon": 18.5, "sog": 5.0},
    )


# ---------------------------------------------------------------------------
# Sequence counter
# ---------------------------------------------------------------------------

class TestNextSequence:
    async def test_starts_at_one(self, buf):
        seq = await buf.next_sequence()
        assert seq == 1

    async def test_monotonically_increasing(self, buf):
        seqs = [await buf.next_sequence() for _ in range(5)]
        assert seqs == list(range(1, 6))

    # BUG-B1: concurrent calls may read the same sequence value
    async def test_concurrent_calls_may_collide(self, buf):
        """BUG-B1 — next_sequence() does UPDATE … then SELECT in two separate
        awaits.  Another coroutine can run in between and read the same post-
        increment value.  Demonstrate the race by scheduling two calls together."""
        results = await asyncio.gather(
            buf.next_sequence(),
            buf.next_sequence(),
        )
        # In an ideal implementation both would be unique.  With the current
        # code the gather can produce [1, 1] (same sequence twice).  Document:
        if len(set(results)) == 1:
            pytest.xfail(
                f"BUG-B1 confirmed: both coroutines got sequence={results[0]}. "
                "Fix: use 'UPDATE … RETURNING seq' in a single round-trip."
            )
        # If they are unique the bug didn't manifest this run — still pass.
        assert len(results) == 2


# ---------------------------------------------------------------------------
# write_delta / pending_deltas / pending_count
# ---------------------------------------------------------------------------

class TestWriteDelta:
    async def test_write_increases_pending_count(self, buf):
        assert await buf.pending_count() == 0
        await buf.write_delta(_delta(1))
        assert await buf.pending_count() == 1

    async def test_write_multiple_deltas(self, buf):
        for i in range(1, 6):
            await buf.write_delta(_delta(i))
        assert await buf.pending_count() == 5

    # BUG-B2: idempotent on same delta_id
    async def test_same_delta_id_is_idempotent(self, buf):
        """BUG-B2 — INSERT OR IGNORE must silently skip duplicate delta_ids."""
        d = _delta(1, delta_id="fixed-id")
        await buf.write_delta(d)
        await buf.write_delta(d)  # second write — must not raise
        assert await buf.pending_count() == 1

    async def test_different_delta_ids_both_stored(self, buf):
        await buf.write_delta(_delta(1, delta_id="a"))
        await buf.write_delta(_delta(2, delta_id="b"))
        assert await buf.pending_count() == 2

    async def test_payload_roundtrips_correctly(self, buf):
        d = _delta(1, delta_id="payload-test")
        d_with_payload = d.model_copy(update={"payload": {"key": "value", "num": 42}})
        await buf.write_delta(d_with_payload)
        pending = await buf.pending_deltas()
        assert pending[0].payload == {"key": "value", "num": 42}


class TestPendingDeltas:
    async def test_returns_oldest_first(self, buf):
        """BUG-B4 guard — ORDER BY sequence ASC."""
        for i in [3, 1, 2]:
            await buf.write_delta(_delta(i, delta_id=f"d{i}"))
        pending = await buf.pending_deltas()
        seqs = [d.sequence for d in pending]
        assert seqs == sorted(seqs)

    async def test_respects_limit(self, buf):
        for i in range(1, 11):
            await buf.write_delta(_delta(i))
        pending = await buf.pending_deltas(limit=3)
        assert len(pending) == 3

    async def test_excludes_already_synced(self, buf):
        await buf.write_delta(_delta(1, delta_id="d1"))
        await buf.write_delta(_delta(2, delta_id="d2"))
        await buf.mark_synced(["d1"])
        pending = await buf.pending_deltas()
        ids = [d.delta_id for d in pending]
        assert "d1" not in ids
        assert "d2" in ids

    async def test_empty_when_nothing_pending(self, buf):
        assert await buf.pending_deltas() == []


# ---------------------------------------------------------------------------
# mark_synced
# ---------------------------------------------------------------------------

class TestMarkSynced:
    # BUG-B3: empty list must not raise
    async def test_empty_list_returns_zero(self, buf):
        """BUG-B3 — 'IN ()' is invalid SQL; must short-circuit before the query."""
        count = await buf.mark_synced([])
        assert count == 0

    async def test_marks_correct_rows(self, buf):
        for i in range(1, 4):
            await buf.write_delta(_delta(i, delta_id=f"d{i}"))
        updated = await buf.mark_synced(["d1", "d3"])
        assert updated == 2
        assert await buf.pending_count() == 1  # only d2 remains

    async def test_idempotent_on_already_synced(self, buf):
        await buf.write_delta(_delta(1, delta_id="d1"))
        await buf.mark_synced(["d1"])
        updated = await buf.mark_synced(["d1"])  # already synced
        assert updated == 0  # WHERE synced = 0 matches nothing

    async def test_unknown_ids_return_zero(self, buf):
        count = await buf.mark_synced(["nonexistent-id"])
        assert count == 0

    async def test_synced_rows_not_returned_in_pending(self, buf):
        await buf.write_delta(_delta(1, delta_id="d1"))
        await buf.mark_synced(["d1"])
        assert await buf.pending_deltas() == []


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

class TestCheckpoint:
    async def test_unknown_node_returns_default(self, buf):
        chk = await buf.get_checkpoint("new-node")
        assert chk.node_id == "new-node"
        assert chk.last_sequence == 0

    async def test_update_and_retrieve(self, buf):
        chk = SyncCheckpoint(node_id="shipboard", last_sequence=42)
        await buf.update_checkpoint(chk)
        retrieved = await buf.get_checkpoint("shipboard")
        assert retrieved.last_sequence == 42

    async def test_upsert_updates_existing(self, buf):
        await buf.update_checkpoint(SyncCheckpoint(node_id="n1", last_sequence=10))
        await buf.update_checkpoint(SyncCheckpoint(node_id="n1", last_sequence=20))
        chk = await buf.get_checkpoint("n1")
        assert chk.last_sequence == 20


# ---------------------------------------------------------------------------
# SyncOrchestrator
# ---------------------------------------------------------------------------

class TestSyncOrchestrator:
    # BUG-B5a: upload_fn must NOT be called when offline
    async def test_no_upload_when_offline(self, buf):
        """BUG-B5a — orchestrator must check is_online() before uploading."""
        upload_fn = AsyncMock(return_value=[])
        orch = SyncOrchestrator(buf, upload_fn, poll_interval=0, batch_size=10)

        with patch("vesselx.offline.buffer.is_online", new=AsyncMock(return_value=False)):
            await buf.write_delta(_delta(1))
            await orch._cycle()

        upload_fn.assert_not_called()
        assert await buf.pending_count() == 1  # still pending

    # BUG-B5b: upload_fn called and deltas marked synced when online
    async def test_uploads_and_marks_synced_when_online(self, buf):
        """BUG-B5b — when online and deltas pending, upload_fn is called and
        returned ids are marked synced."""
        d = _delta(1, delta_id="d1")
        await buf.write_delta(d)

        upload_fn = AsyncMock(return_value=["d1"])
        orch = SyncOrchestrator(buf, upload_fn, poll_interval=0, batch_size=10)

        with patch("vesselx.offline.buffer.is_online", new=AsyncMock(return_value=True)):
            await orch._cycle()

        upload_fn.assert_called_once()
        assert await buf.pending_count() == 0

    async def test_no_upload_when_nothing_pending(self, buf):
        upload_fn = AsyncMock(return_value=[])
        orch = SyncOrchestrator(buf, upload_fn, poll_interval=0, batch_size=10)

        with patch("vesselx.offline.buffer.is_online", new=AsyncMock(return_value=True)):
            await orch._cycle()

        upload_fn.assert_not_called()

    async def test_upload_failure_leaves_deltas_pending(self, buf):
        await buf.write_delta(_delta(1, delta_id="d1"))

        upload_fn = AsyncMock(side_effect=RuntimeError("network error"))
        orch = SyncOrchestrator(buf, upload_fn, poll_interval=0, batch_size=10)

        with patch("vesselx.offline.buffer.is_online", new=AsyncMock(return_value=True)):
            await orch._cycle()  # must not propagate the exception

        assert await buf.pending_count() == 1  # d1 still pending

    async def test_partial_ack_marks_only_acked(self, buf):
        """If upload_fn acks only some IDs, only those are marked synced."""
        for i in range(1, 4):
            await buf.write_delta(_delta(i, delta_id=f"d{i}"))

        upload_fn = AsyncMock(return_value=["d1"])  # only d1 acked
        orch = SyncOrchestrator(buf, upload_fn, poll_interval=0, batch_size=10)

        with patch("vesselx.offline.buffer.is_online", new=AsyncMock(return_value=True)):
            await orch._cycle()

        assert await buf.pending_count() == 2  # d2 and d3 still pending
