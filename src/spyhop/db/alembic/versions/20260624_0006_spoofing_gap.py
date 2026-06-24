"""Add AIS gap kinematic and spoofing signal columns.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-24 20:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vessel_positions",
        sa.Column("gap_type", sa.String(20), nullable=False, server_default=""),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "gap_displacement_nm", sa.Float(), nullable=False, server_default="-1"
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "spoofing_flag", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "spoofing_max_speed_kn", sa.Float(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("vessel_positions", "spoofing_max_speed_kn")
    op.drop_column("vessel_positions", "spoofing_flag")
    op.drop_column("vessel_positions", "gap_displacement_nm")
    op.drop_column("vessel_positions", "gap_type")
