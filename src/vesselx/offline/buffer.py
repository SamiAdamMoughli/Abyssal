"""Offline-first SQLite delta buffer for field deployments.

When a shipboard node loses cloud connectivity (Starlink outage, remote
operations area), this module becomes the sole persistence layer. Track
points, vessel state snapshots, analyst notes, and alert acknowledgements
are stored locally as idempotent delta records. When connectivity returns,
the sync worker replays them upstream without creating duplicates.

Design:
  - WAL journal mode + NORMAL synchronous: crash-safe on ship power cuts,
    fast enough for continuous AIS ping ingestion.
  - Every delta has a monotonic ``sequence`` and a UUID ``delta_id`` so the
    upstream DB can deduplicate on either key.
  - ``mark_synced`` never deletes rows — keeps a full local audit trail.

Usage:
    buffer = OfflineBuffer("/data/vesselx_offline.db")
    await buffer.init()

    # write a track point while disconnected:
    await buffer.write_delta(SyncDelta(
        kind="track_point",
        entity_id=mmsi,
        sequence=await buffer.next_sequence(),
        payload={"lat": lat, "lon": lon, "sog": sog},
    ))

    # on reconnect — upload and mark synced:
    pending = await buffer.pending_deltas(limit=500)
    upload(pending)          # caller's responsibility
    await buffer.mark_synced([d.delta_id for d in pending])
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from vesselx.offline.sync import SyncCheckpoint, SyncDelta

log = logging.getLogger(__name__)

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;

CREATE TABLE IF NOT EXISTS sync_deltas (
    delta_id    TEXT    PRIMARY KEY,
    kind        TEXT    NOT NULL,
    entity_id   TEXT    NOT NULL,
    occurred_at TEXT    NOT NULL,
    origin_node TEXT    NOT NULL DEFAULT 'shipboard',
    sequence    INTEGER NOT NULL,
    payload     TEXT    NOT NULL,
    synced      INTEGER NOT NULL DEFAULT 0,
    synced_at   TEXT
);

-- Covering index for the hot path: pending_deltas() and mark_synced()
CREATE INDEX IF NOT EXISTS idx_deltas_pending
    ON sync_deltas (synced, sequence)
    WHERE synced = 0;

CREATE TABLE IF NOT EXISTS sync_checkpoints (
    node_id       TEXT    PRIMARY KEY,
    last_sequence INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sequence_counter (
    id  INTEGER PRIMARY KEY CHECK (id = 1),
    seq INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO sequence_counter (id, seq) VALUES (1, 0);
"""


class OfflineBuffer:
    """Async SQLite-backed delta queue for disconnected shipboard nodes."""

    def __init__(
        self,
        db_path: str | Path = "/data/vesselx_offline.db",
    ) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Open the database and create tables if needed."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_DDL)
        await self._conn.commit()
        log.info("offline_buffer.init path=%s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Sequence counter
    # ------------------------------------------------------------------

    async def next_sequence(self) -> int:
        """Atomically increment and return the next sequence number."""
        assert self._conn
        await self._conn.execute(
            "UPDATE sequence_counter SET seq = seq + 1 WHERE id = 1"
        )
        await self._conn.commit()
        async with self._conn.execute(
            "SELECT seq FROM sequence_counter WHERE id = 1"
        ) as cur:
            row = await cur.fetchone()
        return row["seq"]

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def write_delta(self, delta: SyncDelta) -> None:
        """Append one delta to the local buffer (idempotent on delta_id)."""
        assert self._conn
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO sync_deltas
              (delta_id, kind, entity_id, occurred_at, origin_node, sequence, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                delta.delta_id,
                delta.kind,
                delta.entity_id,
                delta.occurred_at.isoformat(),
                delta.origin_node,
                delta.sequence,
                json.dumps(delta.payload),
            ),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def pending_deltas(self, limit: int = 500) -> list[SyncDelta]:
        """Return the oldest unsynced deltas in insertion order."""
        assert self._conn
        async with self._conn.execute(
            """
            SELECT delta_id, kind, entity_id, occurred_at,
                   origin_node, sequence, payload
              FROM sync_deltas
             WHERE synced = 0
             ORDER BY sequence ASC
             LIMIT ?
            """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()

        return [
            SyncDelta(
                delta_id=row["delta_id"],
                kind=row["kind"],
                entity_id=row["entity_id"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
                origin_node=row["origin_node"],
                sequence=row["sequence"],
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]

    async def pending_count(self) -> int:
        """Return the number of deltas not yet synced upstream."""
        assert self._conn
        async with self._conn.execute(
            "SELECT COUNT(*) AS n FROM sync_deltas WHERE synced = 0"
        ) as cur:
            row = await cur.fetchone()
        return row["n"] if row else 0

    # ------------------------------------------------------------------
    # Mark synced
    # ------------------------------------------------------------------

    async def mark_synced(self, delta_ids: list[str]) -> int:
        """Mark a confirmed batch as synced.  Returns the row count updated."""
        if not delta_ids:
            return 0
        assert self._conn
        now = datetime.now(timezone.utc).isoformat()
        placeholders = ",".join("?" * len(delta_ids))
        cur = await self._conn.execute(
            f"""
            UPDATE sync_deltas
               SET synced = 1, synced_at = ?
             WHERE delta_id IN ({placeholders})
               AND synced = 0
            """,
            [now, *delta_ids],
        )
        await self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Checkpoint
    # ------------------------------------------------------------------

    async def update_checkpoint(self, checkpoint: SyncCheckpoint) -> None:
        """Persist the latest confirmed upstream sync position for a node."""
        assert self._conn
        await self._conn.execute(
            """
            INSERT INTO sync_checkpoints (node_id, last_sequence, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(node_id) DO UPDATE SET
                last_sequence = excluded.last_sequence,
                updated_at    = excluded.updated_at
            """,
            (
                checkpoint.node_id,
                checkpoint.last_sequence,
                checkpoint.updated_at.isoformat(),
            ),
        )
        await self._conn.commit()

    async def get_checkpoint(self, node_id: str) -> SyncCheckpoint:
        """Load the sync checkpoint for ``node_id``; returns a fresh one if unknown."""
        assert self._conn
        async with self._conn.execute(
            "SELECT node_id, last_sequence, updated_at FROM sync_checkpoints WHERE node_id = ?",
            (node_id,),
        ) as cur:
            row = await cur.fetchone()

        if row:
            return SyncCheckpoint(
                node_id=row["node_id"],
                last_sequence=row["last_sequence"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        return SyncCheckpoint(node_id=node_id)


# ---------------------------------------------------------------------------
# Sync orchestrator
# ---------------------------------------------------------------------------

import asyncio  # noqa: E402 — below class to keep module docstring clean
from collections.abc import Awaitable, Callable

UploadFn = Callable[[list[SyncDelta]], Awaitable[list[str]]]
"""Coroutine type: accepts a delta batch, returns the delta_ids persisted OK."""


class SyncOrchestrator:
    """Background task that drains the local buffer when the satellite link returns.

    Every ``poll_interval`` seconds:
      1. Check connectivity via ``publisher.is_online()``.
      2. If online, pull up to ``batch_size`` pending deltas.
      3. Pass them to ``upload_fn`` (caller supplies the cloud HTTP client).
      4. Mark acknowledged delta_ids as synced in SQLite.

    The upload function must be idempotent — the cloud endpoint should
    ON CONFLICT DO NOTHING keyed on delta_id so no duplicate track points
    appear even when the link flaps mid-batch.
    """

    def __init__(
        self,
        buffer: OfflineBuffer,
        upload_fn: UploadFn,
        poll_interval: float = 30.0,
        batch_size: int = 500,
    ) -> None:
        self._buffer = buffer
        self._upload = upload_fn
        self._interval = poll_interval
        self._batch = batch_size

    async def run(self) -> None:
        log.info("sync_orchestrator.started interval=%.0fs batch=%d", self._interval, self._batch)
        while True:
            try:
                await self._cycle()
            except Exception as exc:
                log.error("sync_orchestrator.cycle_error: %s", exc)
            await asyncio.sleep(self._interval)

    async def _cycle(self) -> None:
        from vesselx.gateway.publisher import is_online

        if not await is_online():
            n = await self._buffer.pending_count()
            if n:
                log.info("sync_orchestrator.offline buffered=%d", n)
            return

        pending = await self._buffer.pending_deltas(limit=self._batch)
        if not pending:
            return

        log.info("sync_orchestrator.uploading count=%d", len(pending))
        try:
            acked_ids = await self._upload(pending)
            synced = await self._buffer.mark_synced(acked_ids)
            log.info("sync_orchestrator.synced count=%d", synced)

            if pending:
                chk = SyncCheckpoint(node_id="shipboard", last_sequence=pending[-1].sequence)
                await self._buffer.update_checkpoint(chk)
        except Exception as exc:
            log.warning("sync_orchestrator.upload_failed: %s", exc)
