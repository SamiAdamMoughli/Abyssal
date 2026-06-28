"""Satellite AIS webhook handlers for the VesselX gateway.

Spire Maritime and Orbcomm deliver AIS vessel positions to customer HTTPS
endpoints via JSON push. Both providers send arrays of records with different
field names; this module normalises them to the VesselX common schema before
publishing to the Redis telemetry stream.

Signature verification:
  Set SPIRE_WEBHOOK_SECRET / ORBCOMM_WEBHOOK_SECRET environment variables to
  enable HMAC-SHA256 request validation. When a secret is not configured the
  check is skipped so development setups work without credentials.

Endpoints mounted on the gateway app:
  POST /webhooks/spire    — Spire Maritime JSON push (array of position objects)
  POST /webhooks/orbcomm  — Orbcomm AIS JSON push (GeoJSON-ish records)
  POST /webhooks/generic  — Flat JSON array with mmsi/lat/lon/sog/cog (testing)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from vesselx.gateway.publisher import publish_raw

log    = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_SPIRE_SECRET   = os.getenv("SPIRE_WEBHOOK_SECRET", "")
_ORBCOMM_SECRET = os.getenv("ORBCOMM_WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------------
# HMAC helpers
# ---------------------------------------------------------------------------

def _verify_hmac(body: bytes, header_value: str, secret: str) -> bool:
    """Return True when the HMAC-SHA256 of ``body`` matches ``header_value``."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()  # type: ignore[attr-defined]
    candidate = header_value.lower().removeprefix("sha256=")
    return hmac.compare_digest(expected, candidate)


def _check_signature(body: bytes, header_value: str | None, secret: str) -> None:
    """Raise 401 when a secret is configured but the signature doesn't match."""
    if not secret:
        return
    if not header_value or not _verify_hmac(body, header_value, secret):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )


# ---------------------------------------------------------------------------
# Provider normalisers
# ---------------------------------------------------------------------------

def _normalize_spire(record: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Spire Maritime position object to the common telemetry schema."""
    try:
        lat = float(record["latitude"])
        lon = float(record["longitude"])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return {
            "source": "spire",
            "mmsi":   str(record["mmsi"]),
            "lat":    lat,
            "lon":    lon,
            "sog":    float(record.get("speed", 0.0)),
            "cog":    float(record.get("course", 0.0)),
            "name":   str(record.get("vessel_name", "")),
            "imo":    str(record.get("imo_number", "")),
            "ts":     str(record.get("timestamp", "")),
        }
    except (KeyError, TypeError, ValueError):
        return None


def _normalize_orbcomm(record: dict[str, Any]) -> dict[str, Any] | None:
    """Map one Orbcomm AIS message to the common telemetry schema.

    Orbcomm wraps coordinates in a GeoJSON ``Position.Point.coordinates``
    array as [lon, lat].
    """
    try:
        coords = (
            record.get("Position", {})
            .get("Point", {})
            .get("coordinates", [None, None])
        )
        lon, lat = float(coords[0]), float(coords[1])
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return {
            "source": "orbcomm",
            "mmsi":   str(record["MMSI"]),
            "lat":    lat,
            "lon":    lon,
            "sog":    float(record.get("SOG", 0.0)),
            "cog":    float(record.get("COG", 0.0)),
            "name":   str(record.get("VesselName", "")),
        }
    except (KeyError, TypeError, ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/spire", status_code=status.HTTP_202_ACCEPTED)
async def spire_webhook(
    request: Request,
    x_spire_signature: str | None = Header(default=None),
) -> dict[str, int]:
    body = await request.body()
    _check_signature(body, x_spire_signature, _SPIRE_SECRET)

    records: list[dict] = await request.json()
    if not isinstance(records, list):
        raise HTTPException(status_code=422, detail="Expected a JSON array")

    accepted = 0
    for record in records:
        payload = _normalize_spire(record)
        if payload:
            await publish_raw(payload)
            accepted += 1

    log.info("webhook.spire accepted=%d total=%d", accepted, len(records))
    return {"accepted": accepted}


@router.post("/orbcomm", status_code=status.HTTP_202_ACCEPTED)
async def orbcomm_webhook(
    request: Request,
    x_orbcomm_signature: str | None = Header(default=None),
) -> dict[str, int]:
    body = await request.body()
    _check_signature(body, x_orbcomm_signature, _ORBCOMM_SECRET)

    records: list[dict] = await request.json()
    if not isinstance(records, list):
        raise HTTPException(status_code=422, detail="Expected a JSON array")

    accepted = 0
    for record in records:
        payload = _normalize_orbcomm(record)
        if payload:
            await publish_raw(payload)
            accepted += 1

    log.info("webhook.orbcomm accepted=%d total=%d", accepted, len(records))
    return {"accepted": accepted}


@router.post("/generic", status_code=status.HTTP_202_ACCEPTED)
async def generic_webhook(request: Request) -> dict[str, int]:
    """Accept a flat JSON array with mmsi, lat, lon, sog, cog fields.

    Used for integration testing and custom AIS adapters that don't match a
    named provider schema.
    """
    records: list[dict] = await request.json()
    if not isinstance(records, list):
        raise HTTPException(status_code=422, detail="Expected a JSON array")

    accepted = 0
    for record in records:
        try:
            lat = float(record["lat"])
            lon = float(record["lon"])
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            payload = {
                "source": "generic",
                "mmsi":   str(record["mmsi"]),
                "lat":    lat,
                "lon":    lon,
                "sog":    float(record.get("sog", 0.0)),
                "cog":    float(record.get("cog", 0.0)),
            }
            await publish_raw(payload)
            accepted += 1
        except (KeyError, TypeError, ValueError):
            continue

    return {"accepted": accepted}
