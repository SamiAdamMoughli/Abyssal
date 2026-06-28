"""asyncio TCP NMEA 0183 listener for local VHF hardware.

Accepts line-delimited NMEA sentences from serial-to-TCP bridges (socat,
ser2net) or VHF transponders that speak IEC 61162-450 directly over Ethernet.
Decoded position records are forwarded to the Redis telemetry stream via
gateway.publisher.

Default TCP port: 10110  (NMEA 0183 over TCP/IP, IEC 61162-450 standard)

Supported sentences:
  !AIVDM / !AIVDO  — AIS VHF Data-Link Message (ITU-R M.1371)
  $GPRMC / $GNRMC  — Recommended Minimum Navigation Information

Multi-part AIS sentences (!AIVDM,2,1,... and !AIVDM,2,2,...) are reassembled
per connection before decoding. Each client gets its own fragment buffer so
sentences from different hardware sources don't cross-contaminate.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from vesselx.gateway.publisher import publish_raw

log = logging.getLogger(__name__)

try:
    from pyais.messages import NMEAMessage as _NMEAMessage
    _PYAIS = True
except ImportError:
    _PYAIS = False
    log.warning(
        "nmea.pyais_unavailable — install pyais to decode !AIVDM sentences; "
        "$GPRMC parsing still works without it"
    )


# ---------------------------------------------------------------------------
# NMEA helper parsers
# ---------------------------------------------------------------------------

def _nmea_latlon(raw: str, direction: str) -> float:
    """Convert a raw NMEA lat/lon field (``DDDMM.MMMM``) to decimal degrees."""
    if not raw or len(raw) < 4:
        return 0.0
    dot = raw.index(".")
    degrees = float(raw[: dot - 2])
    minutes = float(raw[dot - 2 :])
    value   = degrees + minutes / 60.0
    return -value if direction in ("S", "W") else value


def _parse_rmc(sentence: str) -> dict[str, Any] | None:
    """Parse $GPRMC / $GNRMC into a normalised position dict.

    Returns None for any sentence that is not active (field 2 != 'A'),
    malformed, or carries no usable coordinates.
    """
    # Strip checksum and split on commas
    parts = sentence.split("*")[0].split(",")
    if len(parts) < 8 or parts[2] != "A":
        return None
    try:
        lat = _nmea_latlon(parts[3], parts[4])
        lon = _nmea_latlon(parts[5], parts[6])
        sog = float(parts[7]) if parts[7] else 0.0
        cog = float(parts[8]) if len(parts) > 8 and parts[8] else 0.0
        return {
            "source": "nmea_rmc",
            "lat": lat,
            "lon": lon,
            "sog": sog,
            "cog": cog,
        }
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Sentence dispatcher
# ---------------------------------------------------------------------------

async def _dispatch(sentence: str, fragment_buf: dict[str, list[str]]) -> None:
    """Decode one NMEA sentence and publish to the telemetry stream.

    Multi-part AIS sentences are accumulated in ``fragment_buf`` keyed by
    sequential message ID until all parts arrive, then decoded together.
    """
    sentence = sentence.strip()
    if not sentence:
        return

    payload: dict[str, Any] | None = None

    if sentence.startswith(("!AIVDM", "!AIVDO")):
        if not _PYAIS:
            return

        parts = sentence.split("*")[0].split(",")
        total_parts = int(parts[1]) if len(parts) > 2 and parts[1] else 1
        part_num    = int(parts[2]) if len(parts) > 3 and parts[2] else 1
        msg_id      = parts[3] if len(parts) > 4 else ""

        if total_parts == 1:
            fragments = [sentence]
        else:
            buf = fragment_buf.setdefault(msg_id, [])
            buf.append(sentence)
            if len(buf) < total_parts:
                return
            fragments = fragment_buf.pop(msg_id)

        try:
            msgs = [_NMEAMessage(f.encode()) for f in fragments]
            decoded = msgs[0].decode() if len(msgs) == 1 else _NMEAMessage.from_string(fragments)
            mmsi = str(decoded.mmsi) if hasattr(decoded, "mmsi") else None
            lat  = float(decoded.lat)   if hasattr(decoded, "lat")   else None
            lon  = float(decoded.lon)   if hasattr(decoded, "lon")   else None
            sog  = float(decoded.speed) if hasattr(decoded, "speed") else None
            cog  = float(decoded.course) if hasattr(decoded, "course") else None
            payload = {
                "source":   "nmea_ais",
                "mmsi":     mmsi,
                "lat":      lat,
                "lon":      lon,
                "sog":      sog,
                "cog":      cog,
                "msg_type": getattr(decoded, "msg_type", None),
            }
        except Exception as exc:
            log.debug("nmea.ais_decode_err: %s | %.60s", exc, sentence)

    elif "$GPRMC" in sentence or "$GNRMC" in sentence:
        payload = _parse_rmc(sentence)
        if payload:
            payload["source"] = "nmea_rmc"

    if payload and payload.get("lat") is not None and payload.get("lon") is not None:
        try:
            await publish_raw(payload)
        except Exception as exc:
            log.warning("nmea.publish_failed: %s", exc)


# ---------------------------------------------------------------------------
# TCP server
# ---------------------------------------------------------------------------

class NMEATCPServer:
    """asyncio TCP server that ingests NMEA 0183 sentence streams.

    Each connected client gets an isolated fragment buffer so multi-part AIS
    sentences from different hardware sources never collide.
    """

    def __init__(self, host: str = "0.0.0.0", port: int = 10110) -> None:
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None

    async def _on_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername")
        log.info("nmea.client_connected peer=%s", peer)
        fragment_buf: dict[str, list[str]] = {}
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                await _dispatch(line.decode(errors="replace"), fragment_buf)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as exc:
            log.error("nmea.client_error peer=%s err=%s", peer, exc)
        finally:
            writer.close()
            log.info("nmea.client_disconnected peer=%s", peer)

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._on_client,
            self.host,
            self.port,
            limit=64 * 1024,
        )
        log.info("nmea.tcp_listening %s:%d", self.host, self.port)
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
