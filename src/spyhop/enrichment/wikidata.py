"""Wikidata SPARQL fallback — vessel identity by IMO number.

Wikidata property P458 = IMO Ship Identification Number.
No token, no rate limit (fair-use), completely free.

Useful for larger named vessels (container ships, tankers, reefers).
Rare for small fishing boats — that's fine, GFW covers those.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

SPARQL_URL = "https://query.wikidata.org/sparql"
TIMEOUT = 10.0
HEADERS = {
    "Accept": "application/sparql-results+json",
    "User-Agent": "SpyhopMissionRadar/1.0 (sami@spyhop.dev)",
}

# Wikidata entity IDs for common vessel types
_TYPE_MAP = {
    "Q11446":    "cargo",       # ship (generic)
    "Q40218":    "tanker",      # tanker
    "Q178193":   "container",   # container ship
    "Q170173":   "bulk",        # bulk carrier
    "Q11229":    "trawler",     # fishing vessel
    "Q1065139":  "purse_seiner",
    "Q477248":   "reefer",
    "Q207452":   "research",    # research vessel
    "Q16534":    "coast_guard", # patrol vessel
    "Q182726":   "tug",
    "Q839933":   "icebreaker",
    "Q1410328":  "ro_ro",       # ro-ro ship
}

_SPARQL = """
SELECT ?vessel ?vesselLabel ?flag ?flagLabel ?typeQid ?typeLabel ?image ?built WHERE {{
  ?vessel wdt:P458 "{imo}" .
  OPTIONAL {{ ?vessel wdt:P17 ?flag . }}
  OPTIONAL {{ ?vessel wdt:P31 ?type .
              BIND(STRAFTER(STR(?type), "entity/") AS ?typeQid) }}
  OPTIONAL {{ ?vessel wdt:P18 ?image . }}
  OPTIONAL {{ ?vessel wdt:P571 ?built . }}
  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "en" .
  }}
}}
LIMIT 1
"""


async def fetch_by_imo(imo: str | None) -> dict[str, Any]:
    """Return vessel identity fields from Wikidata by IMO number.

    Returns {} if not found or on any error — this is a best-effort fallback.
    """
    if not imo:
        return {}

    # Normalize: strip leading zeros and "IMO" prefix
    imo_clean = re.sub(r"[^0-9]", "", imo).lstrip("0")
    if not imo_clean:
        return {}

    query = _SPARQL.format(imo=imo_clean)
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(
                SPARQL_URL,
                params={"query": query, "format": "json"},
                headers=HEADERS,
            )
            if r.status_code != 200:
                return {}
            data = r.json()
    except Exception:
        return {}

    bindings = data.get("results", {}).get("bindings", [])
    if not bindings:
        return {}

    b = bindings[0]

    def _val(key: str) -> str | None:
        return (b.get(key) or {}).get("value")

    name      = _val("vesselLabel")
    flag_uri  = _val("flag")   # e.g. "http://www.wikidata.org/entity/Q1183"
    type_qid  = _val("typeQid")
    type_lbl  = _val("typeLabel")
    image_url = _val("image")
    built_raw = _val("built")   # ISO datetime string

    # Country code from flag entity — we just pass the Wikidata label
    flag_label = _val("flagLabel")

    built_year = None
    if built_raw:
        m = re.search(r"(\d{4})", built_raw)
        if m:
            built_year = int(m.group(1))

    vessel_type = None
    if type_qid:
        vessel_type = _TYPE_MAP.get(type_qid)
    if not vessel_type and type_lbl:
        vessel_type = type_lbl.lower()

    return {
        "name":        name,
        "flag":        flag_label,   # human-readable country name
        "vessel_type": vessel_type,
        "built_year":  built_year,
        "image_url":   image_url,
        "wikidata_source": True,
    }
