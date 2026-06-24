"""Add trajectory pattern columns to vessel_positions.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-24 18:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vessel_positions",
        sa.Column(
            "trajectory_pattern",
            sa.String(20),
            nullable=False,
            server_default="unknown",
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "trajectory_confidence",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("vessel_positions", "trajectory_confidence")
    op.drop_column("vessel_positions", "trajectory_pattern")
