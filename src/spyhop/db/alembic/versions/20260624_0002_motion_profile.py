"""Add vessel_tracks table and motion-profile columns to vessel_positions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-24 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- vessel_tracks -------------------------------------------------------
    op.create_table(
        "vessel_tracks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mmsi", sa.String(20), nullable=False),
        sa.Column(
            "position",
            Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("sog", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cog", sa.Float(), nullable=False, server_default="0"),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False,
                  server_default="unknown"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_vessel_tracks_mmsi_ts",
        "vessel_tracks",
        ["mmsi", "timestamp"],
    )
    op.create_index(
        "idx_vessel_tracks_gist",
        "vessel_tracks",
        ["position"],
        postgresql_using="gist",
    )

    # --- new columns on vessel_positions -------------------------------------
    op.add_column(
        "vessel_positions",
        sa.Column(
            "behavior_status",
            sa.String(20),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "behavior_confidence",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "cog_degrees",
            sa.Float(),
            nullable=False,
            server_default="-1",
        ),
    )


def downgrade() -> None:
    op.drop_column("vessel_positions", "cog_degrees")
    op.drop_column("vessel_positions", "behavior_confidence")
    op.drop_column("vessel_positions", "behavior_status")
    op.drop_index("idx_vessel_tracks_gist", table_name="vessel_tracks")
    op.drop_index("idx_vessel_tracks_mmsi_ts", table_name="vessel_tracks")
    op.drop_table("vessel_tracks")
