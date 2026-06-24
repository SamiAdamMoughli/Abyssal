"""Initial schema: vessel_positions, iuu_blacklist, sanctioned_vessels.

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostGIS and pg_trgm must already exist (created by init.sql).
    # We do NOT re-create extensions here to avoid permission issues.

    op.create_table(
        "vessel_positions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("mmsi", sa.String(20), nullable=False),
        sa.Column("imo", sa.String(20), nullable=True),
        sa.Column("name", sa.String(150), nullable=False, server_default=""),
        sa.Column(
            "position",
            Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("speed_knots", sa.Float(), nullable=False, server_default="0"),
        sa.Column("flag", sa.String(10), nullable=False, server_default="UNK"),
        sa.Column("vessel_type", sa.String(50), nullable=False, server_default="unknown"),
        sa.Column("ais_gap_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("loitering_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("in_protected_area", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("recent_port_calls", sa.Integer(), nullable=False, server_default="-1"),
        sa.Column("days_since_port", sa.Float(), nullable=False, server_default="-1"),
        sa.Column("distance_to_nearest_port_nm", sa.Float(), nullable=False, server_default="-1"),
        sa.Column("nearby_fishing_vessels", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rendezvous_duration_hours", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ais_vessel_class", sa.String(10), nullable=False, server_default=""),
        sa.Column("risk_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("top_reason_label", sa.String(100), nullable=True),
        sa.Column("reasons_json", postgresql.JSONB(), nullable=True),
        sa.Column("data_source", sa.String(20), nullable=False, server_default="synthetic"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("mmsi", name="uq_vessel_positions_mmsi"),
    )
    op.create_index(
        "idx_vessel_positions_gist", "vessel_positions", ["position"],
        postgresql_using="gist"
    )
    op.create_index("idx_vessel_positions_score", "vessel_positions", ["risk_score"])
    op.create_index("idx_vessel_positions_mmsi", "vessel_positions", ["mmsi"])

    op.create_table(
        "iuu_blacklist",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("listing_source", sa.String(50), nullable=False),
        sa.Column("mmsi", sa.String(20), nullable=True),
        sa.Column("imo", sa.String(20), nullable=True),
        sa.Column("vessel_name", sa.String(200), nullable=True),
        sa.Column("aliases_json", postgresql.JSONB(), nullable=True),
        sa.Column("flag", sa.String(10), nullable=True),
        sa.Column("listing_year", sa.Integer(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_iuu_mmsi", "iuu_blacklist", ["mmsi"])
    op.create_index("idx_iuu_imo", "iuu_blacklist", ["imo"])
    op.create_index(
        "idx_iuu_name_trgm", "iuu_blacklist", ["vessel_name"],
        postgresql_using="gin",
        postgresql_ops={"vessel_name": "gin_trgm_ops"},
    )

    op.create_table(
        "sanctioned_vessels",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("opensanctions_id", sa.String(100), nullable=False),
        sa.Column("vessel_name", sa.String(200), nullable=True),
        sa.Column("aliases_json", postgresql.JSONB(), nullable=True),
        sa.Column("mmsi", sa.String(20), nullable=True),
        sa.Column("imo", sa.String(20), nullable=True),
        sa.Column("flag", sa.String(10), nullable=True),
        sa.Column("sanctions_datasets", postgresql.JSONB(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("opensanctions_id", name="uq_sanctioned_vessels_os_id"),
    )
    op.create_index("idx_sanctions_mmsi", "sanctioned_vessels", ["mmsi"])
    op.create_index("idx_sanctions_imo", "sanctioned_vessels", ["imo"])
    op.create_index(
        "idx_sanctions_name_trgm", "sanctioned_vessels", ["vessel_name"],
        postgresql_using="gin",
        postgresql_ops={"vessel_name": "gin_trgm_ops"},
    )

    # Trigger: auto-update updated_at on vessel_positions row changes.
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = now() AT TIME ZONE 'UTC';
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER trg_vessel_positions_updated_at
        BEFORE UPDATE ON vessel_positions
        FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_vessel_positions_updated_at ON vessel_positions")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")
    op.drop_table("sanctioned_vessels")
    op.drop_table("iuu_blacklist")
    op.drop_index("idx_vessel_positions_gist", "vessel_positions")
    op.drop_index("idx_vessel_positions_score", "vessel_positions")
    op.drop_index("idx_vessel_positions_mmsi", "vessel_positions")
    op.drop_table("vessel_positions")
