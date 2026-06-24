"""Live AIS position consumer — streams from aisstream.io WebSocket.

Run as a standalone process (separate terminal):
    PYTHONPATH=src:. backend/.venv/bin/python -m spyhop.worker.ais_stream

Flow:
  aisstream.io WS  →  parse PositionReport / ShipStaticData
                   →  accumulate in-memory (FLUSH_INTERVAL seconds)
                   →  score with risk engine
                   →  bulk-upsert PostGIS
                   →  pipeline Redis sorted set + publish vessel:updates
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

from spyhop.config import get_settings
from spyhop.logging_config import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.LOG_LEVEL)
log = get_logger(__name__)

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ais-flush")


# ---------------------------------------------------------------------------
# MMSI MID → ISO-3 flag (first 3 digits = Maritime Identification Digit)
# ---------------------------------------------------------------------------

_MID_TO_FLAG: dict[str, str] = {
    "201": "ALB", "202": "AND", "203": "AUT", "204": "AZO", "205": "BEL",
    "206": "BLR", "207": "BGR", "208": "VAT", "209": "CYP", "210": "CYP",
    "211": "DEU", "212": "CYP", "213": "GEO", "214": "MDA", "215": "MLT",
    "216": "ARM", "218": "DEU", "219": "DNK", "220": "DNK", "224": "ESP",
    "225": "ESP", "226": "FRA", "227": "FRA", "228": "FRA", "229": "MLT",
    "230": "FIN", "231": "FRO", "232": "GBR", "233": "GBR", "234": "GBR",
    "235": "GBR", "236": "GIB", "237": "GRC", "238": "HRV", "239": "GRC",
    "240": "GRC", "241": "GRC", "242": "MAR", "243": "HUN", "244": "NLD",
    "245": "NLD", "246": "NLD", "247": "ITA", "248": "MLT", "249": "MLT",
    "250": "IRL", "251": "ISL", "252": "LIE", "253": "LUX", "254": "MCO",
    "255": "PRT", "256": "MLT", "257": "NOR", "258": "NOR", "259": "NOR",
    "261": "POL", "262": "MNE", "263": "PRT", "264": "ROU", "265": "SWE",
    "266": "SWE", "267": "SVK", "268": "SMR", "269": "CHE", "270": "CZE",
    "271": "TUR", "272": "UKR", "273": "RUS", "274": "MKD", "275": "LVA",
    "276": "EST", "277": "LTU", "278": "SVN", "279": "SRB",
    "301": "ATG", "303": "USA", "304": "ATG", "305": "ATG", "306": "CUW",
    "307": "ABW", "308": "BHS", "309": "BHS", "310": "BMU", "311": "BHS",
    "312": "BLZ", "314": "BRB", "316": "CAN", "319": "CYM", "321": "CRI",
    "323": "CUB", "325": "DMA", "327": "DOM", "329": "GLP", "330": "GRD",
    "331": "GRL", "332": "GTM", "334": "HND", "336": "HTI", "338": "USA",
    "339": "JAM", "341": "SLV", "343": "MEX", "345": "MTQ", "347": "MSR",
    "348": "NIC", "350": "PAN", "351": "PAN", "352": "PAN", "353": "PAN",
    "354": "PAN", "355": "PAN", "356": "PAN", "357": "PAN", "358": "PRI",
    "359": "SLV", "361": "KNA", "362": "TTO", "364": "TCA", "366": "USA",
    "367": "USA", "368": "USA", "369": "USA", "370": "PAN", "371": "PAN",
    "372": "PAN", "373": "PAN", "374": "PAN", "375": "VCT", "376": "VGB",
    "377": "VIR", "378": "VGB", "379": "VCT",
    "401": "AFG", "403": "SAU", "405": "BGD", "408": "BHR", "410": "BHR",
    "412": "CHN", "413": "CHN", "414": "CHN", "416": "TWN", "419": "IND",
    "422": "IRN", "423": "AZE", "425": "IRQ", "428": "ISR", "431": "JPN",
    "432": "JPN", "434": "TKM", "436": "KAZ", "437": "UZB", "438": "JOR",
    "440": "KOR", "441": "KOR", "443": "PSE", "445": "PRK", "447": "KWT",
    "450": "LBN", "451": "KYR", "453": "MAC", "455": "MDV", "457": "MNG",
    "459": "NPL", "461": "OMN", "463": "PAK", "466": "QAT", "468": "SYR",
    "470": "ARE", "471": "ARE", "472": "TJK", "473": "YEM", "477": "HKG",
    "478": "BOS", "501": "ATA", "503": "AUS", "506": "MYS", "508": "NRU",
    "510": "PNG", "511": "PNG", "512": "NZL", "514": "SLB", "515": "SLB",
    "516": "CXR", "518": "COK", "520": "FJI", "523": "CCK", "525": "IDN",
    "529": "KIR", "531": "MHL", "533": "FSM", "536": "MNP", "538": "MHL",
    "540": "NCL", "542": "NZL", "544": "NIU", "546": "PYF", "548": "PLW",
    "550": "PNG", "553": "WSM", "555": "SLB", "557": "SLB", "559": "TNG",
    "561": "TUV", "563": "SGP", "564": "SGP", "565": "SGP", "566": "SGP",
    "567": "THA", "570": "VUT", "572": "WLF", "574": "VNM", "576": "PYF",
    "601": "ZAF", "603": "AGO", "605": "DZA", "607": "STP", "608": "GBS",
    "609": "BWA", "610": "COM", "611": "CMR", "612": "CPV", "613": "DJI",
    "616": "EGY", "619": "ERI", "620": "ETH", "621": "GBR", "622": "GAB",
    "624": "GHA", "625": "GMB", "626": "GNB", "627": "GNQ", "629": "GIN",
    "630": "COD", "631": "IVY", "632": "KEN", "633": "LBR", "636": "LBR",
    "637": "LBR", "638": "LBY", "642": "MDG", "644": "MWI", "645": "MLI",
    "647": "MRT", "649": "MUS", "650": "MOZ", "654": "NAM", "655": "NER",
    "656": "NGA", "657": "NGA", "659": "MOZ", "660": "SHN", "661": "SEN",
    "662": "SLE", "663": "SOM", "664": "SSD", "665": "LBR", "666": "SDN",
    "667": "SDN", "668": "SWZ", "669": "SWZ", "670": "TZA", "671": "TGO",
    "672": "TUN", "674": "UGA", "676": "COD", "677": "TZA", "678": "ZMB",
    "679": "ZWE",
    "701": "ARG", "710": "BRA", "720": "BOL", "725": "CHL", "730": "COL",
    "735": "ECU", "740": "FLK", "745": "GUF", "750": "GUY", "755": "PRY",
    "760": "PER", "765": "SUR", "770": "URY", "775": "VEN",
}


def _flag_from_mmsi(mmsi: str) -> str:
    return _MID_TO_FLAG.get(mmsi[:3], "UNK")


# ---------------------------------------------------------------------------
# AIS numeric ship type → internal vessel_type slug
# ---------------------------------------------------------------------------

def _ais_type_to_slug(code: int) -> str:
    if code == 30:
        return "trawler"
    if code in (31, 32, 52):
        return "tug"
    if code == 33:
        return "support"
    if code == 35:
        return "naval"
    if code in (51, 55):
        return "coast_guard"
    if code == 36:
        return "support"   # sailing → support
    if 60 <= code <= 69:
        return "bulk"      # passenger → treat as commercial
    if code in (70, 71, 72, 73, 74, 75, 76, 77, 78, 79):
        return "bulk"
    if code in (80, 81, 82, 83, 84, 85, 86, 87, 88, 89):
        return "tanker"
    return "unknown"


# ---------------------------------------------------------------------------
# Consumer
# ---------------------------------------------------------------------------

class AISConsumer:
    """Buffers incoming AIS messages and flushes them to PostGIS + Redis."""

    def __init__(self) -> None:
        self._positions: dict[str, dict[str, Any]] = {}
        self._static: dict[str, dict[str, Any]] = {}
        self._msg_count = 0
        self._flush_count = 0

    def _bbox_for_subscription(self) -> list:
        """Convert bbox env var to aisstream [[min_lat, min_lon], [max_lat, max_lon]]."""
        raw = settings.AISSTREAM_BBOX or settings.GFW_BBOX
        min_lon, min_lat, max_lon, max_lat = (float(x) for x in raw.split(","))
        return [[[min_lat, min_lon], [max_lat, max_lon]]]

    def handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("MessageType")
        meta = msg.get("MetaData", {})
        mmsi = str(meta.get("MMSI", "")).strip()
        if not mmsi or mmsi == "0":
            return

        self._msg_count += 1

        if msg_type == "PositionReport":
            report = msg.get("Message", {}).get("PositionReport", {})
            lat = report.get("Latitude") if report.get("Latitude") is not None else meta.get("latitude")
            lon = report.get("Longitude") if report.get("Longitude") is not None else meta.get("longitude")
            if lat is None or lon is None:
                return
            lat, lon = float(lat), float(lon)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                return
            sog = float(report.get("Sog") or 0.0)
            cog = float(report.get("Cog") or 0.0)
            ship_name = str(meta.get("ShipName") or "").strip()
            self._positions[mmsi] = {
                "mmsi": mmsi,
                "lat": lat,
                "lon": lon,
                "speed_knots": sog,
                "cog": cog,
                "name": ship_name or self._static.get(mmsi, {}).get("name", ""),
                "flag": _flag_from_mmsi(mmsi),
                "updated_at": datetime.now(timezone.utc),
            }

        elif msg_type == "ShipStaticData":
            static = msg.get("Message", {}).get("ShipStaticData", {})
            name = str(static.get("Name") or meta.get("ShipName") or "").strip()
            self._static[mmsi] = {
                "name": name,
                "vessel_type": _ais_type_to_slug(int(static.get("Type") or 0)),
                "imo": str(static.get("Imo") or ""),
                "flag": _flag_from_mmsi(mmsi),
            }
            # backfill position cache if we already have a position
            if mmsi in self._positions and name:
                self._positions[mmsi]["name"] = name
                self._positions[mmsi]["vessel_type"] = self._static[mmsi]["vessel_type"]

    def flush(self) -> int:
        """Score and persist all buffered positions. Returns vessel count."""
        if not self._positions:
            return 0

        batch = dict(self._positions)
        self._positions.clear()

        from backend.app.risk_engine import Vessel, assess, compound_score
        from backend.app.geo import is_in_protected_area
        from spyhop.worker.tasks import (
            _build_vessel_rows,
            _upsert_vessels_sync,
            _pipeline_update_scores,
            _insert_tracks_sync,
            _compute_behaviors_sync,
            _compute_spatial_sync,
            _compute_trajectories_sync,
            _detect_proximity_sync,
            VESSEL_UPDATES_CHANNEL,
            _sync_redis,
        )
        import ujson

        # Insert position pings into track history
        now_utc = datetime.now(timezone.utc)
        track_rows = [
            {
                "mmsi": mmsi,
                "lat": pos["lat"],
                "lon": pos["lon"],
                "sog": pos["speed_knots"],
                "cog": pos.get("cog", 0.0),
                "timestamp": now_utc,
                "source": "aisstream",
            }
            for mmsi, pos in batch.items()
        ]
        _insert_tracks_sync(track_rows)

        # Compute motion profiles + spatial + trajectory from track history
        mmsi_keys = list(batch.keys())
        behavior_map = _compute_behaviors_sync(mmsi_keys, window_hours=4)
        spatial_map = _compute_spatial_sync(mmsi_keys, window_hours=6)
        trajectory_map = _compute_trajectories_sync(
            mmsi_keys, window_hours=12
        )

        # Pass 1: build Vessel objects (no scoring yet)
        vessel_objs = []
        for mmsi, pos in batch.items():
            static = self._static.get(mmsi, {})
            profile = behavior_map.get(mmsi)
            sf = spatial_map.get(mmsi)
            tp = trajectory_map.get(mmsi)
            lat, lon = pos["lat"], pos["lon"]
            vessel_objs.append(Vessel(
                mmsi=mmsi,
                name=pos.get("name") or static.get("name") or "UNKNOWN",
                lat=lat,
                lon=lon,
                speed_knots=pos["speed_knots"],
                cog_degrees=pos.get("cog", -1.0),
                flag=(
                    pos.get("flag")
                    or static.get("flag")
                    or _flag_from_mmsi(mmsi)
                ),
                vessel_type=static.get("vessel_type") or "unknown",
                in_protected_area=is_in_protected_area(lat, lon),
                ais_gap_hours=0.0,
                loitering_hours=0.0,
                behavior=profile.behavior.value if profile else "unknown",
                behavior_confidence=(
                    profile.confidence if profile else 0.0
                ),
                nearest_mpa_nm=(
                    sf.nearest_mpa_nm if sf is not None else -1.0
                ),
                time_in_zone_hours=(
                    sf.time_in_zone_hours if sf is not None else 0.0
                ),
                border_skirting=(
                    sf.border_skirting if sf is not None else False
                ),
                trajectory_pattern=(
                    tp.pattern.value if tp is not None else "unknown"
                ),
                trajectory_confidence=(
                    tp.confidence if tp is not None else 0.0
                ),
            ))

        # Pass 2: spoofing / gap + context fusion + V2V proximity
        from spyhop.worker.tasks import (  # noqa: PLC0415
            _compute_environmental_sync,
            _compute_spoofing_sync,
            _enrich_vessel_profiles_sync,
        )
        spoofing_map = _compute_spoofing_sync(vessel_objs, window_hours=4)
        for v in vessel_objs:
            sp = spoofing_map.get(v.mmsi)
            if sp is not None:
                v.gap_type = sp.get("gap_type", "")
                v.gap_displacement_nm = sp.get("gap_displacement_nm", -1.0)
                v.spoofing_flag = sp.get("spoofing_flag", False)
                v.spoofing_max_speed_kn = sp.get(
                    "spoofing_max_speed_kn", 0.0
                )

        env_map = _compute_environmental_sync(vessel_objs)
        for v in vessel_objs:
            ec = env_map.get(v.mmsi)
            if ec is not None:
                v.sst_celsius = ec.sst_celsius
                v.wave_height_m = ec.wave_height_m
                v.wind_speed_kn = ec.wind_speed_kn
                v.sst_at_thermal_front = ec.sst_at_thermal_front

        profile_map = _enrich_vessel_profiles_sync(vessel_objs)
        for v in vessel_objs:
            profile = profile_map.get(v.mmsi)
            if profile:
                v.historical_risk_score = profile.get("historical_risk", -1.0)
                vt = profile.get("verified_type", "")
                if vt:
                    v.verified_vessel_type = vt

        proximity_map = _detect_proximity_sync(vessel_objs)
        _FISHING = {
            "fishing", "trawler", "longliner",
            "purse_seiner", "squid_jigger",
        }
        for v in vessel_objs:
            ir = proximity_map.get(v.mmsi)
            if ir is not None:
                v.rendezvous_partner_type = ir.partner_type
                v.rendezvous_meeting_class = ir.meeting_class.value
                v.rendezvous_duration_hours = max(
                    v.rendezvous_duration_hours, ir.duration_h
                )
                if ir.partner_type.lower() in _FISHING:
                    v.nearby_fishing_vessels = max(
                        v.nearby_fishing_vessels, 1
                    )

        # Pass 3: score
        assessments = []
        for v in vessel_objs:
            try:
                ta = compound_score(v)
            except Exception:
                ta = assess(v)
            assessments.append(ta)

        rows = _build_vessel_rows(assessments)
        # stamp data_source as "aisstream" so we can distinguish from GFW rows
        for row in rows:
            row["data_source"] = "aisstream"
        _upsert_vessels_sync(rows)

        score_map = {a.vessel.mmsi: a.score for a in assessments}
        _pipeline_update_scores(score_map)

        payload = [
            {
                "mmsi": a.vessel.mmsi,
                "name": a.vessel.name,
                "lat": a.vessel.lat,
                "lon": a.vessel.lon,
                "risk_score": a.score,
                "top_reason": a.top_reason.label if a.top_reason else None,
                "vessel_type": a.vessel.vessel_type,
                "in_protected_area": a.vessel.in_protected_area,
            }
            for a in assessments
        ]
        _sync_redis.publish(VESSEL_UPDATES_CHANNEL, ujson.dumps(payload))

        self._flush_count += 1
        return len(assessments)


# ---------------------------------------------------------------------------
# Async runner
# ---------------------------------------------------------------------------

async def run(consumer: AISConsumer) -> None:
    api_key = settings.AISSTREAM_API_KEY
    if not api_key:
        log.error("ais_stream.no_key", hint="Set AISSTREAM_API_KEY in .env")
        sys.exit(1)

    bbox = consumer._bbox_for_subscription()
    subscribe_msg = json.dumps({
        "APIKey": api_key,
        "BoundingBoxes": bbox,
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    })

    delay = 2
    loop = asyncio.get_running_loop()

    while True:
        try:
            log.info("ais_stream.connecting", url=AISSTREAM_URL, bbox=bbox)
            async with websockets.connect(
                AISSTREAM_URL,
                ping_interval=20,
                ping_timeout=10,
                open_timeout=15,
            ) as ws:
                await ws.send(subscribe_msg)
                log.info("ais_stream.subscribed", bbox=bbox)
                delay = 2  # reset backoff on successful connect

                async def _flush_loop() -> None:
                    interval = settings.AISSTREAM_FLUSH_INTERVAL
                    while True:
                        await asyncio.sleep(interval)
                        n = await loop.run_in_executor(
                            _executor, consumer.flush
                        )
                        if n:
                            log.info(
                                "ais_stream.flushed",
                                vessels=n,
                                total_msgs=consumer._msg_count,
                                flush_n=consumer._flush_count,
                            )

                flush_task = asyncio.create_task(_flush_loop())
                try:
                    async for raw in ws:
                        consumer.handle(raw)
                except ConnectionClosed as exc:
                    log.warning("ais_stream.closed", reason=str(exc))
                finally:
                    flush_task.cancel()
                    try:
                        await flush_task
                    except asyncio.CancelledError:
                        pass
                    # final flush on disconnect
                    n = await loop.run_in_executor(_executor, consumer.flush)
                    if n:
                        log.info("ais_stream.final_flush", vessels=n)

        except Exception as exc:
            log.warning("ais_stream.error", error=str(exc), reconnect_in=delay)

        log.info("ais_stream.reconnecting", delay=delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


def main() -> None:
    consumer = AISConsumer()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _shutdown(sig, frame):  # noqa: ARG001
        log.info("ais_stream.shutdown", signal=sig)
        for task in asyncio.all_tasks(loop):
            task.cancel()

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    try:
        loop.run_until_complete(run(consumer))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        _executor.shutdown(wait=False)
        loop.close()
        log.info("ais_stream.stopped")


if __name__ == "__main__":
    main()
