"""Add h3_index column (resolution 7) to vessel_positions.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-25 00:00:00.000000

Changes:
  vessel_positions — h3_index VARCHAR(20) + B-tree index
    Stores the Uber H3 cell ID at resolution 7 (~5 km² per hexagon).
    Enables sub-millisecond lookup of all vessels inside a hex cell or a
    polyfill of cells, replacing full-table ST_Within scans for hex queries.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vessel_positions",
        sa.Column("h3_index", sa.String(20), nullable=True),
    )
    op.create_index(
        "idx_vessel_positions_h3",
        "vessel_positions",
        ["h3_index"],
        postgresql_using="btree",
    )


def downgrade() -> None:
    op.drop_index("idx_vessel_positions_h3", table_name="vessel_positions")
    op.drop_column("vessel_positions", "h3_index")
