"""ml_model_registry and ml_prediction_log tables.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ml_model_registry",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("artifact_path", sa.Text, nullable=False),
        sa.Column("metrics_json", JSONB, nullable=True),
        sa.Column("feature_names_json", JSONB, nullable=True),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="shadow",
        ),
        sa.Column(
            "trained_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "promoted_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "retired_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.UniqueConstraint(
            "model_name", "version", name="uq_ml_model_version"
        ),
    )
    op.create_index(
        "idx_ml_registry_name_status",
        "ml_model_registry",
        ["model_name", "status"],
    )

    op.create_table(
        "ml_prediction_log",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True
        ),
        sa.Column("model_name", sa.String(64), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("mmsi", sa.String(20), nullable=False),
        sa.Column("predicted_score", sa.Float, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "idx_ml_pred_log_created",
        "ml_prediction_log",
        ["created_at"],
    )
    op.create_index(
        "idx_ml_pred_log_mmsi_model",
        "ml_prediction_log",
        ["mmsi", "model_name"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_ml_pred_log_mmsi_model", table_name="ml_prediction_log"
    )
    op.drop_index(
        "idx_ml_pred_log_created", table_name="ml_prediction_log"
    )
    op.drop_table("ml_prediction_log")
    op.drop_index(
        "idx_ml_registry_name_status", table_name="ml_model_registry"
    )
    op.drop_table("ml_model_registry")
