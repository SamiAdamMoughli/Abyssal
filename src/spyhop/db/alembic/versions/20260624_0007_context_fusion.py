"""Add contextual fusion columns and environment_raster table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-24 21:00:00.000000

Changes:
  vessel_positions  — 6 new contextual fusion columns
  environment_raster — new table for hourly SST / wave / wind raster
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- vessel_positions: contextual fusion columns -------------------------
    op.add_column(
        "vessel_positions",
        sa.Column("sst_celsius", sa.Float(), nullable=False, server_default="-999"),
    )
    op.add_column(
        "vessel_positions",
        sa.Column("wave_height_m", sa.Float(), nullable=False, server_default="-1"),
    )
    op.add_column(
        "vessel_positions",
        sa.Column("wind_speed_kn", sa.Float(), nullable=False, server_default="-1"),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "sst_at_thermal_front",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "historical_risk_score", sa.Float(), nullable=False, server_default="-1"
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "verified_vessel_type", sa.String(50), nullable=False, server_default=""
        ),
    )

    # --- environment_raster table --------------------------------------------
    op.create_table(
        "environment_raster",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "position",
            Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("sst_celsius", sa.Float(), nullable=False),
        sa.Column("wave_height_m", sa.Float(), nullable=False),
        sa.Column("wind_speed_kn", sa.Float(), nullable=False),
        sa.Column("valid_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_env_raster_gist",
        "environment_raster",
        ["position"],
        postgresql_using="gist",
    )
    op.create_index(
        "idx_env_raster_valid",
        "environment_raster",
        ["valid_time"],
    )


def downgrade() -> None:
    op.drop_index("idx_env_raster_valid", table_name="environment_raster")
    op.drop_index("idx_env_raster_gist", table_name="environment_raster")
    op.drop_table("environment_raster")

    for col in (
        "verified_vessel_type",
        "historical_risk_score",
        "sst_at_thermal_front",
        "wind_speed_kn",
        "wave_height_m",
        "sst_celsius",
    ):
        op.drop_column("vessel_positions", col)
