"""Alembic env.py — targets spyhop.db.models.Base.metadata."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make sure ``spyhop`` is importable when alembic runs from the repo root.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from spyhop.config import get_settings  # noqa: E402
from spyhop.db.engine import Base  # noqa: E402
import spyhop.db.models  # noqa: F401 — ensures models are registered on Base.metadata

settings = get_settings()

alembic_cfg = context.config
if alembic_cfg.config_file_name is not None:
    fileConfig(alembic_cfg.config_file_name)

# Override the URL with the *sync* driver (psycopg2) for alembic migrations.
alembic_cfg.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit migration SQL without a live DB connection (used by CI diff checks)."""
    url = alembic_cfg.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB (default path)."""
    connectable = engine_from_config(
        alembic_cfg.get_section(alembic_cfg.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
