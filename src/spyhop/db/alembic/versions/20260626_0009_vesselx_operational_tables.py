"""Add VesselX operational tables: vesselx_alerts and field_deployments.

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-26 00:00:00.000000

Changes
-------
vesselx_alerts
    Persisted findings from the brain rule evaluator. One row per triggered
    rule per evaluation cycle. alert_id is a client-generated UUID written
    by the brain task so ingestion is idempotent (ON CONFLICT DO NOTHING).
    Zone A only: no person-level data. The acknowledged_* columns are written
    exclusively by analyst-initiated API calls, never automatically.

field_deployments
    Registry of offline-capable shipboard mini-PC nodes. Each node running
    VesselX in offline-first mode (Sea Shepherd campaigns, remote ops) has
    one row here. last_sync_sequence lets the cloud replay only the delta
    records the node produced after the last confirmed upstream sync.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # vesselx_alerts
    # ------------------------------------------------------------------
    op.create_table(
        "vesselx_alerts",
        sa.Column("id",         sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("alert_id",   sa.String(36),   nullable=False),
        sa.Column("rule_id",    sa.String(50),   nullable=False),
        sa.Column("rule_label", sa.String(100),  nullable=False),
        sa.Column("severity",   sa.String(20),   nullable=False),
        sa.Column("message",    sa.Text(),        nullable=False),

        # Zone A fields — spatial + kinematic only
        sa.Column("mmsi",     sa.String(20),  nullable=True),
        sa.Column("lat",      sa.Float(),     nullable=True),
        sa.Column("lon",      sa.Float(),     nullable=True),
        sa.Column("h3_index", sa.String(20),  nullable=True),

        # Analyst acknowledgement (Zone B boundary — analyst-only write)
        sa.Column("acknowledged",    sa.Boolean(),               nullable=False, server_default=sa.text("false")),
        sa.Column("acknowledged_by", sa.String(100),             nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at",   sa.DateTime(timezone=True), server_default=sa.func.now()),

        sa.UniqueConstraint("alert_id", name="uq_vesselx_alerts_id"),
    )
    op.create_index("idx_vxa_mmsi",      "vesselx_alerts", ["mmsi"])
    op.create_index("idx_vxa_rule",      "vesselx_alerts", ["rule_id"])
    op.create_index("idx_vxa_severity",  "vesselx_alerts", ["severity"])
    op.create_index("idx_vxa_triggered", "vesselx_alerts", ["triggered_at"])

    # ------------------------------------------------------------------
    # field_deployments
    # ------------------------------------------------------------------
    op.create_table(
        "field_deployments",
        sa.Column("id",          sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("node_id",     sa.String(100),  nullable=False),
        sa.Column("vessel_mmsi", sa.String(20),   nullable=True),
        sa.Column("vessel_name", sa.String(150),  nullable=True),

        sa.Column("last_seen_at",       sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_sync_sequence", sa.Integer(),  nullable=False, server_default=sa.text("0")),
        sa.Column("operational_area",   sa.Text(),     nullable=True),
        sa.Column("metadata_json",      postgresql.JSONB(), nullable=True),

        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),

        sa.UniqueConstraint("node_id", name="uq_field_deployments_node"),
    )
    op.create_index("idx_fd_vessel_mmsi", "field_deployments", ["vessel_mmsi"])


def downgrade() -> None:
    op.drop_index("idx_fd_vessel_mmsi", table_name="field_deployments")
    op.drop_table("field_deployments")

    op.drop_index("idx_vxa_triggered", table_name="vesselx_alerts")
    op.drop_index("idx_vxa_severity",  table_name="vesselx_alerts")
    op.drop_index("idx_vxa_rule",      table_name="vesselx_alerts")
    op.drop_index("idx_vxa_mmsi",      table_name="vesselx_alerts")
    op.drop_table("vesselx_alerts")
