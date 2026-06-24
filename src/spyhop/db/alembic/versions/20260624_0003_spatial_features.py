"""Add spatial risk columns to vessel_positions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-24 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vessel_positions",
        sa.Column(
            "nearest_mpa_nm",
            sa.Float(),
            nullable=False,
            server_default="-1",
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "time_in_zone_hours",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "border_skirting",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("vessel_positions", "border_skirting")
    op.drop_column("vessel_positions", "time_in_zone_hours")
    op.drop_column("vessel_positions", "nearest_mpa_nm")
