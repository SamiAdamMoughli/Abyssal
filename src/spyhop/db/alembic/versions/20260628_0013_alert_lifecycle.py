"""Add last_seen_at and resolved_at to vesselx_alerts for alert lifecycle.

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vesselx_alerts",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "vesselx_alerts",
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Partial index makes "fetch all open alerts" (resolved_at IS NULL) cheap.
    op.create_index(
        "idx_vxa_resolved",
        "vesselx_alerts",
        ["resolved_at"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_vxa_resolved", table_name="vesselx_alerts")
    op.drop_column("vesselx_alerts", "resolved_at")
    op.drop_column("vesselx_alerts", "last_seen_at")
