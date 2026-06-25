"""Enrichment Manager — Worker 2 of the three-container architecture.

Drains the `enrich:queue` Redis LIST that the AIS stream worker fills with
unknown MMSIs. For each MMSI it runs the cascading API fallback chain
(GFW → Wikidata) and stores the identity profile in Redis with a 30-day TTL.

Key design choices:
- 1.5 s pause between API calls — respects GFW's rate limits.
- BLPOP with timeout instead of polling — zero CPU when queue is empty.
- Dedup guard: skips MMSIs that already have a cached identity profile.
- Runs as a standalone async process; no Celery dependency.

Run:
    PYTHONPATH=src:. python -m spyhop.worker.enrichment_manager
"""

from __future__ import annotations

import asyncio
import signal
import sys
from typing import Any

import redis as sync_redis_lib
import ujson

from spyhop.config import get_settings
from spyhop.logging_config import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.LOG_LEVEL)
log = get_logger(__name__)

QUEUE_KEY = "enrich:queue"
IDENTITY_TTL = 86_400 * 30   # 30 days
RATE_LIMIT_S = 1.5            # seconds between external API calls
BLPOP_TIMEOUT = 5             # seconds to wait for a queue item

_redis = sync_redis_lib.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_timeout=5,
    socket_connect_timeout=5,
)


async def _enrich_one(mmsi: str) -> None:
    """Run the cascading fallback chain for a single MMSI."""
    identity_key = f"identity:{mmsi}"

    # Skip if already enriched (another worker beat us to it)
    if _redis.exists(identity_key):
        log.debug("enrichment_manager.skip_cached", mmsi=mmsi)
        return

    # Minimal vessel dict — the identity enrichers only need MMSI + hint IMO
    vessel_hint: dict[str, Any] = {
        "mmsi": mmsi,
        "imo": None,
        "name": None,
        "flag": None,
        "vessel_type": None,
    }

    # Try to pull the existing DB record for a richer starting point
    try:
        from spyhop.db.engine import AsyncSessionLocal
        from spyhop.db.repository import VesselRepository
        async with AsyncSessionLocal() as session:
            repo = VesselRepository(session)
            vessel = await repo.get_by_mmsi(mmsi)
            if vessel:
                vessel_hint = vessel.to_dict()
    except Exception as exc:
        log.debug("enrichment_manager.db_miss", mmsi=mmsi, error=str(exc))

    # Run cascading identity resolution
    from spyhop.enrichment.gfw import fetch_identity
    from spyhop.enrichment.wikidata import fetch_by_imo

    imo = vessel_hint.get("imo")

    gfw, wiki = await asyncio.gather(
        fetch_identity(mmsi),
        fetch_by_imo(imo),
        return_exceptions=True,
    )
    if isinstance(gfw,  Exception):
        gfw = {}
    if isinstance(wiki, Exception):
        wiki = {}

    # Re-query Wikidata with a better IMO if GFW resolved one
    gfw_imo = gfw.get("imo") if isinstance(gfw, dict) else None
    if gfw_imo and gfw_imo != imo:
        try:
            wiki = await fetch_by_imo(gfw_imo)
        except Exception:
            pass

    def first(*vals):
        for v in vals:
            if v not in (None, "", "unknown", "Unknown"):
                return v
        return None

    identity = {
        "imo":        first(gfw.get("imo"), imo),
        "flag":       first(gfw.get("flag"),        wiki.get("flag"),        vessel_hint.get("flag")),
        "vessel_type":first(gfw.get("vessel_type"), wiki.get("vessel_type"), vessel_hint.get("vessel_type")),
        "length_m":   first(gfw.get("length_m")),
        "tonnage_gt": first(gfw.get("tonnage_gt")),
        "owner":      first(gfw.get("owner")),
        "callsign":   first(gfw.get("callsign")),
        "built_year": first(gfw.get("built_year"), wiki.get("built_year")),
        "image_url":  first(wiki.get("image_url")),
        "gfw_id":     first(gfw.get("gfw_id")),
        "_sources":   [s for s, d in [("gfw", gfw), ("wikidata", wiki)]
                       if isinstance(d, dict) and d],
    }

    _redis.setex(identity_key, IDENTITY_TTL, ujson.dumps(identity))
    log.info(
        "enrichment_manager.enriched",
        mmsi=mmsi,
        sources=identity["_sources"],
        flag=identity["flag"],
    )


async def run() -> None:
    """Main loop — drain the enrichment queue with rate limiting."""
    log.info("enrichment_manager.starting", queue=QUEUE_KEY)

    while True:
        # Blocking pop — waits up to BLPOP_TIMEOUT seconds, returns None if empty
        item = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _redis.blpop(QUEUE_KEY, timeout=BLPOP_TIMEOUT)
        )
        if item is None:
            continue  # timeout — queue is empty, loop again

        _, mmsi = item   # blpop returns (key, value)
        mmsi = mmsi.strip()
        if not mmsi:
            continue

        try:
            await _enrich_one(mmsi)
        except Exception as exc:
            log.warning("enrichment_manager.error", mmsi=mmsi, error=str(exc))

        # Rate-limit: never hammer external APIs
        await asyncio.sleep(RATE_LIMIT_S)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(sig, _frame):
        log.info("enrichment_manager.shutdown", signal=sig)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGINT,  _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(run())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        loop.close()
        log.info("enrichment_manager.stopped")


if __name__ == "__main__":
    main()
