"""Centralised settings — loaded once per process from environment / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---------------------------------------------------------
    APP_NAME: str = "VesselX"
    APP_VERSION: str = "1.0.0"
    LOG_LEVEL: str = "INFO"
    DATA_SOURCE: Literal["synthetic", "gfw"] = "synthetic"

    # --- PostgreSQL (async — FastAPI) ----------------------------------------
    DATABASE_URL: str = (
        "postgresql+asyncpg://vesselx:vesselx@vesselx-core-db:5432/vesselx"
    )
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 40
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800  # recycle connections every 30 min

    # --- PostgreSQL (sync — Celery) -------------------------------------------
    SYNC_DATABASE_URL: str = (
        "postgresql+psycopg2://vesselx:vesselx@vesselx-core-db:5432/vesselx"
    )

    # --- Redis ---------------------------------------------------------------
    REDIS_URL: str = "redis://vesselx-telemetry-cache:6379/0"
    REDIS_MAX_CONNECTIONS: int = 50
    REDIS_SOCKET_TIMEOUT: float = 5.0
    REDIS_SOCKET_CONNECT_TIMEOUT: float = 5.0

    # --- Celery --------------------------------------------------------------
    CELERY_BROKER_URL: str = "redis://vesselx-telemetry-cache:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://vesselx-telemetry-cache:6379/2"

    # --- Cache TTLs (seconds) ------------------------------------------------
    VESSEL_CACHE_TTL: int = 60          # bbox result cache
    SCORE_SORTED_SET_TTL: int = 3600    # Redis sorted set auto-expire

    # --- GFW API tokens (optional — only needed for live AIS data) -----------
    GFW_API_TOKEN: str = ""
    GFW_API_KEY: str = ""
    GFW_API_ORIGIN: str = "http://localhost"
    GFW_BBOX: str = "-91.5,-1.0,-90.0,0.3"
    GFW_HTTP_TIMEOUT: int = 60

    # --- aisstream.io (live AIS WebSocket) -----------------------------------
    AISSTREAM_API_KEY: str = ""
    AISSTREAM_BBOX: str = ""          # overrides GFW_BBOX when set
    AISSTREAM_FLUSH_INTERVAL: int = 10  # seconds between PostGIS flushes

    # --- SSE stream ----------------------------------------------------------
    VESSEL_STREAM_POLL_SECONDS: float = 3.0

    # --- Protected-area source -----------------------------------------------
    PROTECTED_AREA_SOURCE: str = "gfw"

    # --- ML pipeline ---------------------------------------------------------
    MODEL_ARTIFACTS_PATH: str = "/app/model_artifacts"
    # skip training below this row count
    ML_MIN_TRAIN_SAMPLES: int = 50
    # minimum R² to auto-promote risk scorer
    ML_PROMOTE_MIN_R2: float = 0.20
    # PSI thresholds for drift monitoring
    ML_DRIFT_PSI_WARN: float = 0.10
    ML_DRIFT_PSI_CRITICAL: float = 0.20


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
