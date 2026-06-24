#!/usr/bin/env bash
# =============================================================================
# Spyhop local dev bootstrap — run once as a user with sudo access.
# Usage:  bash setup.sh
# =============================================================================
set -euo pipefail

VENV=/home/sami/dev/Abyssal/backend/.venv
PG_VERSION=18
DB_USER=spyhop
DB_PASS=spyhop
DB_NAME=spyhop

echo "=== [1/4] Installing system packages (PostGIS + Redis) ==="
sudo apt-get install -y \
    postgresql-${PG_VERSION}-postgis-3 \
    postgresql-${PG_VERSION}-postgis-3-scripts \
    redis-server

echo ""
echo "=== [2/4] Starting Redis ==="
sudo systemctl enable redis-server
sudo systemctl start redis-server
redis-cli ping && echo "Redis: OK"

echo ""
echo "=== [3/4] Creating PostgreSQL user + database ==="
# Run as postgres superuser via peer auth
sudo -u postgres psql <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS}';
    RAISE NOTICE 'Role ${DB_USER} created.';
  ELSE
    ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASS}';
    RAISE NOTICE 'Role ${DB_USER} already exists — password updated.';
  END IF;
END
\$\$;
SQL

sudo -u postgres psql <<SQL
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')
\gexec
SQL

sudo -u postgres psql -d ${DB_NAME} <<SQL
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
GRANT ALL ON SCHEMA public TO ${DB_USER};
SELECT extname, extversion FROM pg_extension WHERE extname IN ('postgis','pg_trgm','unaccent');
SQL

echo ""
echo "=== [4/4] Checking pg_hba.conf for local password auth ==="
PG_HBA=$(sudo -u postgres psql -tAc "SHOW hba_file;")
echo "pg_hba.conf: $PG_HBA"

# Add md5 / scram-sha-256 auth for spyhop user if not already present
if ! sudo grep -q "spyhop" "$PG_HBA" 2>/dev/null; then
    echo "host    ${DB_NAME}    ${DB_USER}    127.0.0.1/32    scram-sha-256" \
        | sudo tee -a "$PG_HBA"
    sudo systemctl reload postgresql
    echo "Added spyhop auth rule and reloaded PostgreSQL."
fi

echo ""
echo "=== Verifying connection ==="
PGPASSWORD=${DB_PASS} psql -h 127.0.0.1 -U ${DB_USER} -d ${DB_NAME} \
    -c "SELECT PostGIS_Version();" 2>/dev/null || \
    echo "NOTE: Connection via 127.0.0.1 failed — try 'sudo systemctl reload postgresql' and re-run."

echo ""
echo "=== Done! Next steps (run from project root): ==="
echo ""
echo "  # Run Alembic migrations"
echo "  cd /home/sami/dev/Abyssal"
echo "  PYTHONPATH=src:. DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASS}@127.0.0.1/${DB_NAME} \\"
echo "    SYNC_DATABASE_URL=postgresql+psycopg2://${DB_USER}:${DB_PASS}@127.0.0.1/${DB_NAME} \\"
echo "    ${VENV}/bin/alembic upgrade head"
echo ""
echo "  # Start the API"
echo "  PYTHONPATH=src:. DATA_SOURCE=synthetic \\"
echo "    DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASS}@127.0.0.1/${DB_NAME} \\"
echo "    REDIS_URL=redis://127.0.0.1:6379/0 \\"
echo "    ${VENV}/bin/uvicorn spyhop.api.main:app --reload --host 0.0.0.0 --port 8000"
echo ""
echo "  # Start Celery worker (separate terminal)"
echo "  PYTHONPATH=src:. \\"
echo "    SYNC_DATABASE_URL=postgresql+psycopg2://${DB_USER}:${DB_PASS}@127.0.0.1/${DB_NAME} \\"
echo "    REDIS_URL=redis://127.0.0.1:6379/0 CELERY_BROKER_URL=redis://127.0.0.1:6379/1 \\"
echo "    ${VENV}/bin/celery -A spyhop.worker.celery_app worker --loglevel=info -Q default,scoring,sync"
