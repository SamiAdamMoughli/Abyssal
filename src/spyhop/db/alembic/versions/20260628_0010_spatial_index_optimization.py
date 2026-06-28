"""Spatial index optimisation pass.

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-28 00:00:00.000000

Changes
-------
vessel_tracks
  - DROP  idx_vessel_tracks_gist (GiST on position)
      Never used: every track query filters by mmsi + timestamp, never by
      spatial proximity. The index added write overhead to every AIS ping
      insert without benefiting any read path.
  - ADD   idx_vessel_tracks_ts_brin (BRIN on timestamp)
      BRIN costs ~8 KB on a table with millions of rows vs ~40 MB for a
      B-tree. Because rows are appended in roughly timestamp order the
      correlation is near-perfect, letting the planner skip entire 128-page
      heap blocks when pruning (DELETE WHERE timestamp < cutoff).

vesselx_alerts
  - ADD   idx_vxa_mmsi_triggered (mmsi, triggered_at DESC)
      Covers the hot "all alerts for vessel X newest-first" query in one
      index scan instead of a bitmap merge of two single-column indexes.
  - ADD   idx_vxa_unacknowledged (triggered_at) WHERE acknowledged = false
      Partial index for the alert dashboard "pending alerts" view — only
      unacknowledged rows are indexed, so the index stays tiny even as
      acknowledged alerts accumulate.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- vessel_tracks: drop dead spatial index, add cheap BRIN --------------
    op.drop_index("idx_vessel_tracks_gist", table_name="vessel_tracks")

    op.create_index(
        "idx_vessel_tracks_ts_brin",
        "vessel_tracks",
        ["timestamp"],
        postgresql_using="brin",
        # pages_per_range=128 is the default; each summary covers 128 heap
        # pages (~1 MB). For a table with typical 8 kB pages that means one
        # BRIN entry per ~16K rows — more than enough precision for a 7-day
        # prune window on a time-ordered stream.
    )

    # --- vesselx_alerts: composite index + partial unacked index -------------
    op.create_index(
        "idx_vxa_mmsi_triggered",
        "vesselx_alerts",
        ["mmsi", sa.text("triggered_at DESC")],
        postgresql_using="btree",
    )

    op.create_index(
        "idx_vxa_unacknowledged",
        "vesselx_alerts",
        ["triggered_at"],
        postgresql_using="btree",
        postgresql_where=sa.text("acknowledged = false"),
    )


def downgrade() -> None:
    op.drop_index("idx_vxa_unacknowledged", table_name="vesselx_alerts")
    op.drop_index("idx_vxa_mmsi_triggered", table_name="vesselx_alerts")
    op.drop_index("idx_vessel_tracks_ts_brin", table_name="vessel_tracks")

    op.create_index(
        "idx_vessel_tracks_gist",
        "vessel_tracks",
        ["position"],
        postgresql_using="gist",
    )
