"""Add vessel-to-vessel encounter columns to vessel_positions.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-24 19:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vessel_positions",
        sa.Column(
            "rendezvous_partner_type",
            sa.String(30),
            nullable=False,
            server_default="",
        ),
    )
    op.add_column(
        "vessel_positions",
        sa.Column(
            "rendezvous_meeting_class",
            sa.String(30),
            nullable=False,
            server_default="",
        ),
    )


def downgrade() -> None:
    op.drop_column("vessel_positions", "rendezvous_meeting_class")
    op.drop_column("vessel_positions", "rendezvous_partner_type")
