"""VesselX spatial engine and client-facing operational API."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from spyhop.api.routes import alerts, auth, detail, health, vessels, websocket
from spyhop.api.routes import h3 as h3_routes
from spyhop.cache.redis_client import close_pool, wait_for_redis
from spyhop.config import get_settings
from spyhop.db.engine import wait_for_db
from spyhop.logging_config import configure_logging, get_logger
from vesselx import __version__

settings = get_settings()
configure_logging(settings.LOG_LEVEL)
log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    log.info("vesselx.spatial_engine.starting", version=__version__)
    # Background — don't block uvicorn startup so Railway's health probe
    # gets a 200 immediately while DB/Redis finish connecting.
    asyncio.create_task(wait_for_db())
    asyncio.create_task(wait_for_redis())
    log.info("vesselx.spatial_engine.ready")
    yield
    await close_pool()
    log.info("vesselx.spatial_engine.stopped")


app = FastAPI(
    title="VesselX Spatial Indexing & Telemetry Cache",
    version=__version__,
    description=(
        "Maps vessel coordinates onto H3 cells, PostGIS geometry, Redis hot "
        "state, and client-facing maritime operational views."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(vessels.router)
app.include_router(h3_routes.router)
app.include_router(detail.router)
app.include_router(websocket.router)
app.include_router(alerts.router)


@app.get("/service")
async def service() -> dict[str, object]:
    return {
        "service": "vesselx-spatial-engine",
        "version": __version__,
        "capabilities": [
            "h3_indexing",
            "bbox_queries",
            "hot_telemetry_cache",
            "protected_area_lookup",
            "websocket_fanout",
        ],
    }
