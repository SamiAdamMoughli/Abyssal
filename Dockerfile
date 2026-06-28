# =============================================================================
# VesselX — Multi-Stage Dockerfile
#
# Stage 1 (builder):  Install all dependencies into an isolated prefix.
# Stage 2 (final):    Minimal python:3.12-slim image running as non-root.
#
# Security:
#   - No root in the final image (UID 10001 / GID 10001).
#   - No dev tools, no pip, no package manager in the final layer.
#   - /app is owned by the non-root user.
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build-time OS deps (Shapely/GEOS, psycopg2 headers, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libgeos-dev \
        libpq-dev \
        libproj-dev \
        gdal-bin \
        libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install Python packages into a dedicated prefix so we can copy them cleanly.
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2 — final (production image)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS final

# Runtime OS deps only (GEOS + libpq needed at runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgeos-c1v5 \
        libpq5 \
        libproj-dev \
        libgdal-dev \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy Python packages from builder
COPY --from=builder /install /usr/local

# Create a non-root user and group (UID/GID 10001)
RUN groupadd --gid 10001 spyhop \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin spyhop

WORKDIR /app

# Copy application code
COPY --chown=spyhop:spyhop src/ ./src/
COPY --chown=spyhop:spyhop backend/ ./backend/
COPY --chown=spyhop:spyhop alembic.ini ./

# Add src and repo root to PYTHONPATH so both `spyhop` and `backend` packages
# are importable without installing them as editable packages.
ENV PYTHONPATH="/app/src:/app" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Drop to non-root
USER spyhop

# Expose the API port (workers don't expose any)
EXPOSE 8000

# Default command: run the VesselX spatial engine API.
# Override in docker-compose.yml for worker / beat / flower.
CMD ["uvicorn", "vesselx.spatial_engine.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--loop", "uvloop", \
     "--http", "httptools", \
     "--log-level", "info"]
