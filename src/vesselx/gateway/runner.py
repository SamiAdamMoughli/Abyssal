"""Headless runner for vesselx-gateway.

Starts both the FastAPI HTTP server (webhooks + management API) and the
NMEA TCP server (via the FastAPI lifespan). Run directly or via a process
supervisor:

    python -m vesselx.gateway.runner
    uvicorn vesselx.gateway.app:app --host 0.0.0.0 --port 8001
"""

from __future__ import annotations

import uvicorn

from vesselx.gateway.app import app


def main() -> None:
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,
        log_level="info",
    )


if __name__ == "__main__":
    main()
