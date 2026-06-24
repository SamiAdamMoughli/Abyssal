"""FastAPI application entry point for Mission Radar (Spyhop).

Lifespan context:
  - Waits for DB and Redis to be reachable (with exponential backoff).
  - Warms up the static in-memory indices (IUU list, sanctions, EEZ) from
    the JSON caches that the Celery sync tasks maintain.
  - Tears down the Redis connection pool cleanly on shutdown.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from spyhop.api.routes import health, vessels, websocket
from spyhop.cache.redis_client import close_pool, wait_for_redis
from spyhop.config import get_settings
from spyhop.db.engine import wait_for_db
from spyhop.logging_config import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.LOG_LEVEL)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup / shutdown lifecycle for the FastAPI process."""
    log.info("spyhop.api.starting", version=settings.APP_VERSION)

    # --- Wait for infrastructure (exponential backoff) ----------------------
    await wait_for_db()
    await wait_for_redis()

    # --- Warm up in-memory rule indices from JSON caches --------------------
    _warmup_rule_indices()

    log.info("spyhop.api.ready")
    yield

    # --- Graceful shutdown ---------------------------------------------------
    await close_pool()
    log.info("spyhop.api.stopped")


def _warmup_rule_indices() -> None:
    """Pre-load IUU/sanctions/EEZ indices so first requests are cache-hits."""
    try:
        from backend.app.sources import eez, iuu_list, opensanctions, port_control
        for mod in (iuu_list, opensanctions, port_control, eez):
            try:
                mod.warmup()
                log.info("warmup.ok", source=getattr(mod, "SOURCE", mod.__name__))
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "warmup.failed",
                    source=getattr(mod, "SOURCE", mod.__name__),
                    error=str(exc),
                )
    except ImportError as exc:
        log.warning("warmup.import_error", error=str(exc))


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title=settings.APP_NAME,
    description=(
        "Decision-support for combating IUU fishing. "
        "Every risk score comes with a human-readable explanation."
    ),
    version=settings.APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(vessels.router)
app.include_router(websocket.router)
