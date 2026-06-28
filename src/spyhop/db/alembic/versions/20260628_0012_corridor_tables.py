"""Corridor analysis tables: vessel_position_snapshots + h3_risk_corridors.

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vessel_position_snapshots",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("mmsi", sa.String(20), nullable=False),
        sa.Column("h3_index_7", sa.String(20), nullable=True),
        sa.Column("h3_index_5", sa.String(20), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("flag", sa.String(10), nullable=False, server_default="UNK"),
        sa.Column("vessel_type", sa.String(50), nullable=False, server_default="unknown"),
        sa.Column("ais_gap_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loitering_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("in_protected_area", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rendezvous_duration_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("spoofing_flag", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("snapped_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "idx_snapshots_h3_5_snapped",
        "vessel_position_snapshots",
        ["h3_index_5", "snapped_at"],
    )
    op.create_index(
        "idx_snapshots_mmsi_snapped",
        "vessel_position_snapshots",
        ["mmsi", "snapped_at"],
    )
    op.create_index(
        "idx_snapshots_snapped",
        "vessel_position_snapshots",
        ["snapped_at"],
    )

    op.create_table(
        "h3_risk_corridors",
        sa.Column("h3_cell", sa.String(20), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("vessel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("high_risk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("med_risk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dark_vessel_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rendezvous_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mpa_incursion_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_risk_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_risk_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("dominant_flag", sa.String(10), nullable=True),
        sa.Column("dominant_vessel_type", sa.String(50), nullable=True),
        sa.Column("persistence_weeks", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("corridor_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column(
            "materialized_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("h3_cell", "week_start", name="pk_h3_risk_corridors"),
    )
    op.create_index(
        "idx_h3_corridors_score",
        "h3_risk_corridors",
        ["corridor_score"],
    )
    op.create_index(
        "idx_h3_corridors_week",
        "h3_risk_corridors",
        ["week_start"],
    )


def downgrade() -> None:
    op.drop_table("h3_risk_corridors")
    op.drop_table("vessel_position_snapshots")
