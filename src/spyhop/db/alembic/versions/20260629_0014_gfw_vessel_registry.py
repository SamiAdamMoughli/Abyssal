"""Add gfw_vessel_registry table and gfw_vessel_latest materialized view.

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gfw_vessel_registry",
        sa.Column("mmsi", sa.String(20), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("flag_ais", sa.String(10), nullable=True),
        sa.Column("flag_registry", sa.String(10), nullable=True),
        sa.Column("flag_gfw", sa.String(10), nullable=True),
        sa.Column("vessel_class_inferred", sa.String(50), nullable=True),
        sa.Column("vessel_class_inferred_score", sa.Float(), nullable=True),
        sa.Column("vessel_class_registry", sa.String(50), nullable=True),
        sa.Column("vessel_class_gfw", sa.String(50), nullable=True),
        sa.Column("self_reported_fishing_vessel", sa.Boolean(), nullable=True),
        sa.Column("length_m_gfw", sa.Float(), nullable=True),
        sa.Column("engine_power_kw_gfw", sa.Float(), nullable=True),
        sa.Column("tonnage_gt_gfw", sa.Float(), nullable=True),
        sa.Column("registries_listed", sa.Text(), nullable=True),
        sa.Column("active_hours", sa.Float(), nullable=True),
        sa.Column("fishing_hours", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("mmsi", "year", name="pk_gfw_vessel_registry"),
    )
    op.create_index("idx_gfw_registry_mmsi", "gfw_vessel_registry", ["mmsi"])

    # GFW enrichment columns on vessel_positions
    t = "vessel_positions"
    op.add_column(t, sa.Column("gfw_geartype", sa.String(50), nullable=True))
    op.add_column(t, sa.Column("gfw_flag", sa.String(10), nullable=True))
    op.add_column(t, sa.Column("gfw_length_m", sa.Float(), nullable=True))
    op.add_column(t, sa.Column("gfw_engine_kw", sa.Float(), nullable=True))
    op.add_column(t, sa.Column("gfw_tonnage_gt", sa.Float(), nullable=True))
    op.add_column(t, sa.Column("gfw_fishing_hours", sa.Float(), nullable=True))
    op.add_column(t, sa.Column("gfw_active_hours", sa.Float(), nullable=True))
    op.add_column(t, sa.Column("gfw_registries", sa.Text(), nullable=True))
    op.add_column(
        t,
        sa.Column("gfw_self_reported_fishing", sa.Boolean(), nullable=True),
    )

    # Materialized view: most recent record per MMSI (max year)
    op.execute("""
        CREATE MATERIALIZED VIEW gfw_vessel_latest AS
        SELECT DISTINCT ON (mmsi)
            mmsi, year, flag_ais, flag_registry, flag_gfw,
            vessel_class_inferred, vessel_class_inferred_score,
            vessel_class_registry, vessel_class_gfw,
            self_reported_fishing_vessel,
            length_m_gfw, engine_power_kw_gfw, tonnage_gt_gfw,
            registries_listed, active_hours, fishing_hours
        FROM gfw_vessel_registry
        ORDER BY mmsi, year DESC
        WITH DATA;
    """)
    op.execute(
        "CREATE UNIQUE INDEX idx_gfw_vessel_latest_mmsi"
        " ON gfw_vessel_latest (mmsi);"
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS gfw_vessel_latest;")
    op.drop_index("idx_gfw_registry_mmsi", table_name="gfw_vessel_registry")
    op.drop_table("gfw_vessel_registry")
