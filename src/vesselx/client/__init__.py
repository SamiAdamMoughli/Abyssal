"""VesselX Python client SDK — thin wrapper around the spatial engine API."""

from __future__ import annotations

import os
from typing import Any

import httpx

_DEFAULT_BASE = os.environ.get(
    "VESSELX_API_URL", "http://vesselx-spatial-engine:8000"
)
_DEFAULT_TIMEOUT = 30.0


class VesselXClient:
    """Synchronous client for the VesselX spatial engine.

    Usage::

        from vesselx.client import VesselXClient
        client = VesselXClient()
        vessels = client.get_vessels(min_lat=-1, max_lat=1, min_lon=-91, max_lon=-89)
    """

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE,
        token: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.Client(
            base_url=base_url, headers=headers, timeout=timeout
        )

    def health(self) -> dict[str, str]:
        return self._http.get("/health").raise_for_status().json()

    def get_vessels(
        self,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        start: str | None = None,
        end: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "min_lat": min_lat,
            "max_lat": max_lat,
            "min_lon": min_lon,
            "max_lon": max_lon,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return self._http.get("/vessels", params=params).raise_for_status().json()

    def get_vessel(self, mmsi: str) -> dict[str, Any]:
        return (
            self._http.get(f"/vessels/{mmsi}").raise_for_status().json()
        )

    def get_h3_counts(
        self,
        h3_ids: list[str],
        resolution: int = 4,
    ) -> dict[str, int]:
        return (
            self._http.post(
                "/h3/counts",
                json={"h3_ids": h3_ids, "resolution": resolution},
            )
            .raise_for_status()
            .json()
        )

    def top_targets(self, limit: int = 10) -> list[dict[str, Any]]:
        return (
            self._http.get("/vessels/top-targets", params={"limit": limit})
            .raise_for_status()
            .json()
        )

    def login(self, username: str, password: str) -> str:
        """Authenticate and return the JWT access token."""
        data = {"username": username, "password": password}
        resp = self._http.post("/auth/token", data=data).raise_for_status()
        return resp.json()["access_token"]

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "VesselXClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class AsyncVesselXClient:
    """Async variant for use inside FastAPI / asyncio services."""

    def __init__(
        self,
        base_url: str = _DEFAULT_BASE,
        token: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        headers: dict[str, str] = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._http = httpx.AsyncClient(
            base_url=base_url, headers=headers, timeout=timeout
        )

    async def health(self) -> dict[str, str]:
        r = await self._http.get("/health")
        return r.raise_for_status().json()

    async def get_vessels(
        self,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
    ) -> list[dict[str, Any]]:
        params = {
            "min_lat": min_lat,
            "max_lat": max_lat,
            "min_lon": min_lon,
            "max_lon": max_lon,
        }
        r = await self._http.get("/vessels", params=params)
        return r.raise_for_status().json()

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "AsyncVesselXClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
