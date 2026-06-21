"""Read-only Verifikation der GFW-API-Antwort gegen unsere Mapping-Annahmen.

Aendert NICHTS am Code/Mapping. Macht kleine, schreibfreie Abrufe und zeigt, ob
unsere angenommenen Feldnamen (in gfw_vessels.py) zur echten Antwort passen.

Start (aus backend/):  .venv/bin/python verify_gfw.py
Ohne GFW_API_TOKEN bricht es sauber ab.
"""

from __future__ import annotations

import json
from typing import Any, Dict

import requests
from dotenv import load_dotenv

from app import gfw_vessels as g

load_dotenv()


def _short(obj: Any, n: int = 400) -> str:
    return json.dumps(obj, ensure_ascii=False)[:n]


def check(label: str, ok: bool) -> None:
    print(f"   [{'OK ' if ok else 'XX '}] {label}")


def main() -> None:
    try:
        token = g._get_token()
    except g.GfwApiError as e:
        print("Kein Token -> Verifikation uebersprungen:", e)
        return

    print(f"Base: {g.GFW_API_BASE}")
    print(f"Token: ...{token[-6:]} (Laenge {len(token)})\n")

    # ---------------------------------------------------------------- #
    # 1) Auth-Smoke-Test ueber den verifizierten GET /vessels/search
    # ---------------------------------------------------------------- #
    print("== 1) GET /vessels/search (Auth-Test) ==")
    try:
        r = requests.get(
            f"{g.GFW_API_BASE}{g.VESSELS_SEARCH_ENDPOINT}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            params={"query": "fishing", "datasets[0]": g.VESSEL_IDENTITY_DATASET, "limit": 1},
            timeout=g.HTTP_TIMEOUT_SECONDS,
        )
        print(f"   HTTP {r.status_code}")
        if r.status_code == 401:
            print("   -> 401: Token ungueltig/abgelaufen. Restliche Tests machen keinen Sinn.")
            return
        if r.ok:
            data = r.json()
            print("   Top-Level-Keys:", list(data.keys()))
            entries = data.get("entries") or data.get("data") or []
            check("Ergebnis-Liste unter 'entries'", "entries" in data)
            if entries:
                v = entries[0]
                vkeys = list(v.keys())
                print("   1. Vessel-Keys:", vkeys[:15])
                check("vessel hat 'ssvid' (unser mmsi)", "ssvid" in v)
                check("vessel hat 'name'", "name" in v or "shipname" in v)
                check("vessel hat 'flag'", "flag" in v)
        else:
            print("   Body:", _short(r.text))
    except requests.RequestException as e:
        print("   Netzwerkfehler:", e)
        return

    # ---------------------------------------------------------------- #
    # 2) POST /events: Body-Form + Antwortstruktur pruefen
    # ---------------------------------------------------------------- #
    print("\n== 2) POST /events (Body-/Antwort-Struktur) ==")
    bbox = (-90.7, -0.6, -90.4, -0.3)  # winzige bbox
    start, end = g._default_timeframe()
    print(f"   bbox={bbox}  start={start}  end={end}")
    body: Dict[str, Any] = {
        "datasets": g.EVENT_DATASETS,
        "startDate": start,
        "endDate": end,
        "geometry": g._bbox_to_geojson_polygon(bbox),
    }
    try:
        r = requests.post(
            f"{g.GFW_API_BASE}{g.EVENTS_ENDPOINT}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                     "Content-Type": "application/json"},
            json=body, params={"limit": 5, "offset": 0},
            timeout=g.HTTP_TIMEOUT_SECONDS,
        )
        print(f"   HTTP {r.status_code}")
        if r.status_code in (400, 422):
            print("   -> Body/Parameter passen nicht. Antwort (Hinweis auf richtige Felder):")
            print("   ", _short(r.text, 600))
            return
        if not r.ok:
            print("   Body:", _short(r.text))
            return
        data = r.json()
        print("   Top-Level-Keys:", list(data.keys()))
        check("Events unter 'entries'", "entries" in data)
        entries = data.get("entries") or data.get("data") or []
        print(f"   #Events: {len(entries)}")
        if entries:
            ev = entries[0]
            print("   1. Event-Keys:", list(ev.keys()))
            check("event hat 'type'", "type" in ev)
            check("event hat 'position'", "position" in ev)
            if isinstance(ev.get("position"), dict):
                check("position hat 'lat'/'lon'", "lat" in ev["position"] and "lon" in ev["position"])
            check("event hat 'vessel'", "vessel" in ev)
            if isinstance(ev.get("vessel"), dict):
                check("vessel hat 'ssvid'", "ssvid" in ev["vessel"])
            # Welche type-Enums kommen wirklich vor?
            types = sorted({str(e.get("type")) for e in entries})
            print("   vorkommende type-Werte:", types)
            print(f"   (unsere Annahmen: GAP={g.EVENT_TYPE_GAP!r}, LOITERING={g.EVENT_TYPE_LOITERING!r})")
    except requests.RequestException as e:
        print("   Netzwerkfehler:", e)


if __name__ == "__main__":
    main()
