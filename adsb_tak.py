#!/usr/bin/env python3
"""
adsb_tak.py - Poll ADS-B and push aircraft to a TAK server as CoT.

Two groups are tracked:
  - CBU fleet aircraft, everywhere, all the time (looked up by tail number).
  - Any other aircraft departing Riverside Municipal (KRAL), for its first
    flight out only: followed from takeoff until it lands somewhere, then
    not picked up again for the rest of that local day.

Public data only (adsb.fi open data). Part of the ~/TAK project.
"""

import asyncio
import json
import math
import os
from configparser import ConfigParser
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET

import aiohttp
import pytak

# CBU fleet tail numbers (from CBU's public VA aviation docs)
CBU_TAILS = {
    "N935CB", "N944CB", "N207CB", "N233CB",
    "N255CB", "N246CB", "N248CB", "N259CB",
    "N374CB", "N249CB", 

}

# Riverside Municipal Airport (KRAL) - CBU flies out of here
KRAL_LAT = 33.9519
KRAL_LON = -117.4459
SEARCH_RADIUS_NM = 100      # how far around KRAL to look
POLL_SECONDS = 10          # how often to poll ADS-B
STALE_SECONDS = 60         # how long a marker lives without an update

# Free, no-auth ADS-B feed (https://github.com/adsbfi/opendata). Rate limit
# is 1 request/second, so the requests in one poll are spaced by REQUEST_GAP.
ADSB_BASE = "https://opendata.adsb.fi/api/v2"
ADSB_URL = f"{ADSB_BASE}/lat/{KRAL_LAT}/lon/{KRAL_LON}/dist/{SEARCH_RADIUS_NM}"
REQUEST_GAP_SECONDS = 1.1

# --- KRAL departures -------------------------------------------------------
# Runway-area position and field elevation, distinct from KRAL_LAT/LON above
# (the search centre), so the 2 nm departure ring sits on the field itself.
KRAL_FIELD_LAT = 33.9511
KRAL_FIELD_LON = -117.4410
KRAL_ELEV_FT = 819
LOCAL_TZ = ZoneInfo("America/Los_Angeles")   # "once per day" uses local days

# A climb-out is caught inside this ring, below this altitude, at this rate.
# The ring stays well inside the 2.9 nm to Flabob (KRIR), and the nearest-
# airport check below rejects anything that is closer to another field.
DEPART_RADIUS_NM = 2.0
DEPART_MAX_ALT_FT = KRAL_ELEV_FT + 1500
DEPART_MIN_CLIMB_FPM = 300
# Seen on the ground at KRAL, then airborne within this ring = takeoff.
LIFTOFF_RADIUS_NM = 3.0
GROUND_MEMORY_SECONDS = 1800

# A tracked departure ends after a full-stop landing anywhere (on the ground,
# or slower than any fixed-wing flies, for this long - shorter is a
# touch-and-go and the flight continues) or after this long with no data.
LANDED_GS_KT = 35
FULL_STOP_SECONDS = 60
LOST_SECONDS = 600

STATE_FILE = Path(__file__).resolve().with_name("adsb_state.json")
AIRPORTS_FILE = Path(__file__).resolve().parent / "webmap" / "data" / "airports.geojson"

# Where to send CoT: OpenTAKServer's plain-TCP streaming port on this box.
# transit_tak.py, weather_tak.py and the web map's listener all reuse this.
COT_URL = "tcp://127.0.0.1:8088"


SELF_UID = "CBU-ADSB-FEED"
PRESENCE_SECONDS = 120     # how often to re-announce ourselves to the server


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class TxStallWatchdog:
    """Exit the feeder when FreeTAKServer has silently stopped reading from it.

    FTS sometimes keeps a reconnected client's socket open but never reads it
    again (seen 2026-09-25: transit_tak's socket backed up ~1MB while it kept
    logging "Sent 16 buses", and the web map got nothing for 30 min). No
    exception ever fires in that state, so systemd's Restart=always never
    kicks in. The only visible symptom is pytak's outbound queue staying full
    - in a healthy link the TXWorker drains it immediately. Checked once per
    poll cycle; several full checks in a row means a dead link, so exit and
    let systemd reconnect from scratch.
    """

    def __init__(self, queue, logger, limit=4):
        self._queue = queue
        self._logger = logger
        self._limit = limit
        self._strikes = 0

    def check(self):
        self._strikes = self._strikes + 1 if self._queue.full() else 0
        if self._strikes >= self._limit:
            self._logger.error(
                "TX queue full for %d polls in a row - FTS stopped reading, exiting to reconnect",
                self._strikes,
            )
            os._exit(1)


def distance_nm(lat1, lon1, lat2, lon2):
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 3440.065 * 2 * math.asin(math.sqrt(a))


def load_other_airports():
    """(lat, lon) of every known aerodrome except KRAL, for the nearest-field
    check. Missing data just disables that check."""
    try:
        features = json.loads(AIRPORTS_FILE.read_text()).get("features", [])
    except (OSError, json.JSONDecodeError):
        return []
    return [
        (f["geometry"]["coordinates"][1], f["geometry"]["coordinates"][0])
        for f in features
        if f["properties"].get("icao") != "KRAL"
    ]


def is_cbu(ac):
    return (ac.get("r") or "").upper() in CBU_TAILS


class KralDepartures:
    """Decides which non-CBU aircraft count as KRAL departures right now.

    active: hex -> {since, last_seen, landed_at} for flights being followed.
    done:   hex -> local date whose first flight out has already been tracked.
    Persisted to STATE_FILE so a restart mid-flight keeps following it.
    """

    def __init__(self, other_airports=(), state_file=None):
        self.other_airports = list(other_airports)
        self.state_file = state_file
        self.active = {}
        self.done = {}
        self.ground_at_kral = {}   # hex -> last time seen on the ground at KRAL

    # -- geometry ---------------------------------------------------------
    def _kral_nm(self, ac):
        return distance_nm(ac["lat"], ac["lon"], KRAL_FIELD_LAT, KRAL_FIELD_LON)

    def _kral_is_nearest(self, ac, kral_nm):
        return all(
            distance_nm(ac["lat"], ac["lon"], lat, lon) > kral_nm
            for lat, lon in self.other_airports
        )

    @staticmethod
    def _on_surface(ac):
        if ac.get("alt_baro") == "ground":
            return True
        gs = ac.get("gs")
        return gs is not None and float(gs) < LANDED_GS_KT

    # -- per-poll update --------------------------------------------------
    def update(self, aircraft, now):
        """Feed one poll's observations (any mix, CBU included); returns the
        set of hexes currently tracked as KRAL departures."""
        today = now.astimezone(LOCAL_TZ).date().isoformat()
        self.done = {h: d for h, d in self.done.items() if d == today}
        self.ground_at_kral = {
            h: t for h, t in self.ground_at_kral.items()
            if (now - t).total_seconds() < GROUND_MEMORY_SECONDS
        }

        for ac in aircraft:
            hex_id = (ac.get("hex") or "").lower()
            if not hex_id or is_cbu(ac) or ac.get("lat") is None or ac.get("lon") is None:
                continue
            surface = self._on_surface(ac)

            flight = self.active.get(hex_id)
            if flight is not None:
                flight["last_seen"] = now
                if not surface:
                    flight["landed_at"] = None
                elif flight["landed_at"] is None:
                    flight["landed_at"] = now
                elif (now - flight["landed_at"]).total_seconds() >= FULL_STOP_SECONDS:
                    self._finish(hex_id, today)
                continue

            if hex_id in self.done:
                continue

            kral_nm = self._kral_nm(ac)
            if kral_nm > LIFTOFF_RADIUS_NM or not self._kral_is_nearest(ac, kral_nm):
                continue
            if surface:
                if kral_nm <= DEPART_RADIUS_NM:
                    self.ground_at_kral[hex_id] = now
                continue

            lifted_off = hex_id in self.ground_at_kral
            alt = ac.get("alt_baro")
            climbing_out = (
                kral_nm <= DEPART_RADIUS_NM
                and isinstance(alt, (int, float)) and alt <= DEPART_MAX_ALT_FT
                and (ac.get("baro_rate") or ac.get("geom_rate") or 0) >= DEPART_MIN_CLIMB_FPM
            )
            if lifted_off or climbing_out:
                self.active[hex_id] = {"since": now, "last_seen": now, "landed_at": None}
                self.ground_at_kral.pop(hex_id, None)

        for hex_id, flight in list(self.active.items()):
            if (now - flight["last_seen"]).total_seconds() > LOST_SECONDS:
                self._finish(hex_id, today)
        return set(self.active)

    def _finish(self, hex_id, today):
        self.active.pop(hex_id, None)
        self.done[hex_id] = today

    # -- persistence ------------------------------------------------------
    def save(self):
        if not self.state_file:
            return
        payload = {
            "active": {
                h: {k: (v.isoformat() if v else None) for k, v in f.items()}
                for h, f in self.active.items()
            },
            "done": self.done,
        }
        tmp = self.state_file.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(payload))
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    def load(self):
        if not self.state_file or not self.state_file.exists():
            return
        try:
            payload = json.loads(self.state_file.read_text())
            self.done = dict(payload.get("done", {}))
            self.active = {
                h: {k: (datetime.fromisoformat(v) if v else None) for k, v in f.items()}
                for h, f in payload.get("active", {}).items()
            }
        except (OSError, ValueError, AttributeError, TypeError):
            self.active, self.done = {}, {}


def make_presence():
    """Self-presence CoT, the way a real TAK client announces itself.

    FreeTAKServer only relays a client's CoT on to *other* connected clients
    once that client has registered itself this way. pytak's default hello is
    a bare 't-x-d-d' ping, which FTS logs as 'takPing' and then errors on - so
    without this, aircraft events reach the server but are never forwarded to
    anything (confirmed by testing: a registered listener received 0 events
    from an unregistered sender, and all of them once the sender registered).
    """
    now = datetime.now(timezone.utc)
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", SELF_UID)
    event.set("type", "a-f-G-U-C")   # ground/friendly/unit/combat - a "device"
    event.set("how", "m-g")
    event.set("time", iso(now))
    event.set("start", iso(now))
    event.set("stale", iso(now + timedelta(seconds=PRESENCE_SECONDS * 2)))

    point = ET.SubElement(event, "point")
    point.set("lat", str(KRAL_LAT))
    point.set("lon", str(KRAL_LON))
    point.set("hae", "0.0")
    point.set("ce", "9999999.0")
    point.set("le", "9999999.0")

    detail = ET.SubElement(event, "detail")
    takv = ET.SubElement(detail, "takv")
    takv.set("os", "linux")
    takv.set("version", "1.0")
    takv.set("device", "adsb_tak.py")
    takv.set("platform", "CBU-ADSB-Feed")
    contact = ET.SubElement(detail, "contact")
    contact.set("callsign", SELF_UID)
    contact.set("endpoint", "*:-1:stcp")
    ET.SubElement(detail, "uid").set("Droid", SELF_UID)
    group = ET.SubElement(detail, "__group")
    group.set("name", "Cyan")
    group.set("role", "Team Member")
    ET.SubElement(detail, "status").set("battery", "100")
    track = ET.SubElement(detail, "track")
    track.set("course", "0.0")
    track.set("speed", "0.0")
    return ET.tostring(event)


def make_cot(ac, departed_kral=None):
    """Turn one aircraft dict into a CoT XML bytestring.

    departed_kral is the takeoff time for a tracked KRAL departure, None for
    a CBU aircraft. The two groups get different uid prefixes and CoT types
    (CBU friendly/blue, departures neutral) plus a fleet attribute the web
    map colours by.
    """
    now = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=STALE_SECONDS)

    hex_id = (ac.get("hex") or "").strip()
    # Plenty of GA traffic has no registration in the feed; the hex is the
    # only stable id then, and keeps them from colliding as one "UNKNOWN".
    reg = (ac.get("r") or hex_id.upper() or "UNKNOWN").strip()
    kral = departed_kral is not None
    lat = ac.get("lat")
    lon = ac.get("lon")
    if lat is None or lon is None:
        return None

    alt_baro = ac.get("alt_baro", 0)
    on_ground = alt_baro == "ground"
    alt_ft = 0 if on_ground else float(alt_baro or 0)
    hae = alt_ft * 0.3048

    gs_kt = float(ac.get("gs", 0) or 0)
    speed_ms = gs_kt * 0.514444
    course = float(ac.get("track", 0) or 0)
    callsign = (ac.get("flight") or reg).strip()

    ac_type = (ac.get("t") or "").strip()
    owner = (ac.get("ownOp") or "").strip()

    event = ET.Element("event")
    event.set("version", "2.0")
    if kral:
        event.set("uid", f"KRAL-{hex_id}")
        event.set("type", "a-n-A-C-F")   # air/neutral/aircraft/civ/fixed-wing
    else:
        event.set("uid", f"CBU-{reg}")
        event.set("type", "a-f-A-C-F")   # air/friendly/aircraft/civ/fixed-wing
    event.set("how", "m-g")
    event.set("time", iso(now))
    event.set("start", iso(now))
    event.set("stale", iso(stale))

    point = ET.SubElement(event, "point")
    point.set("lat", str(lat))
    point.set("lon", str(lon))
    point.set("hae", str(hae))
    point.set("ce", "9999999.0")
    point.set("le", "9999999.0")

    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact").set("callsign", callsign)
    track = ET.SubElement(detail, "track")
    track.set("course", str(course))
    track.set("speed", str(speed_ms))

    # Custom, spec-legal detail child. Real TAK clients ignore unknown detail
    # elements, so this is free extra data for our own webmap backend to read
    # back out of FTS without breaking ATAK/WinTAK compatibility.
    aircraft = ET.SubElement(detail, "aircraft")
    aircraft.set("reg", reg)
    aircraft.set("type", ac_type)
    aircraft.set("owner", owner)
    aircraft.set("hex", hex_id)
    # Carried explicitly because hae=0 alone can't distinguish "on the
    # ground" from "no altitude reported", and the web map needs the
    # ground->airborne transition to anchor a flight trail at the runway.
    aircraft.set("ground", "true" if on_ground else "false")
    aircraft.set("fleet", "kral" if kral else "cbu")
    if kral:
        aircraft.set("dep", "KRAL")
        aircraft.set("dep_time", departed_kral.isoformat())

    label = "KRAL departure" if kral else "CBU aircraft"
    remarks = f"{label} {reg} | alt {int(alt_ft)} ft | gs {int(gs_kt)} kt"
    if ac_type:
        remarks += f" | type {ac_type}"
    if owner:
        remarks += f" | owner {owner}"
    ET.SubElement(detail, "remarks").text = remarks
    return ET.tostring(event)


async def fetch(session, url):
    headers = {"User-Agent": "cbu-tak-demo"}
    async with session.get(url, headers=headers, timeout=15) as resp:
        data = await resp.json()
    return data.get("aircraft") or data.get("ac") or []


async def fetch_all(session, tracked_hexes):
    """One poll's observations, deduplicated by hex: everything near KRAL
    (where departures are spotted), the CBU fleet wherever it is, and any
    tracked departure that has since flown out of the search radius."""
    seen = {}

    def add(aircraft):
        for ac in aircraft:
            hex_id = (ac.get("hex") or "").lower()
            if hex_id:
                seen[hex_id] = ac

    add(await fetch(session, ADSB_URL))
    await asyncio.sleep(REQUEST_GAP_SECONDS)
    add(await fetch(session, f"{ADSB_BASE}/registration/{','.join(sorted(CBU_TAILS))}"))
    missing = sorted(set(tracked_hexes) - set(seen))
    if missing:
        await asyncio.sleep(REQUEST_GAP_SECONDS)
        add(await fetch(session, f"{ADSB_BASE}/hex/{','.join(missing)}"))
    return list(seen.values())


class ADSBSender(pytak.Worker):
    async def run(self):
        last_presence = 0.0
        watchdog = TxStallWatchdog(self.queue, self._logger)
        departures = KralDepartures(load_other_airports(), STATE_FILE)
        departures.load()
        async with aiohttp.ClientSession() as session:
            while True:
                watchdog.check()
                try:
                    now = asyncio.get_event_loop().time()
                    if now - last_presence > PRESENCE_SECONDS:
                        await self.put_queue(make_presence())
                        last_presence = now
                        self._logger.info("Announced presence to TAK server")

                    aircraft = await fetch_all(session, departures.active)
                    tracked = departures.update(aircraft, datetime.now(timezone.utc))
                    departures.save()

                    sent_cbu = sent_kral = 0
                    for ac in aircraft:
                        hex_id = (ac.get("hex") or "").lower()
                        if is_cbu(ac):
                            cot = make_cot(ac)
                            sent_cbu += cot is not None
                        elif hex_id in tracked:
                            cot = make_cot(ac, departures.active[hex_id]["since"])
                            sent_kral += cot is not None
                        else:
                            continue
                        if cot:
                            await self.put_queue(cot)
                    self._logger.info(
                        "Sent %d CBU aircraft and %d KRAL departures to TAK", sent_cbu, sent_kral
                    )
                except Exception as e:
                    self._logger.warning("Poll failed: %s", e)
                await asyncio.sleep(POLL_SECONDS)


async def main():
    config = ConfigParser()
    config["adsbtak"] = {"COT_URL": COT_URL}
    cfg = config["adsbtak"]
    clitool = pytak.CLITool(cfg)
    await clitool.setup()
    clitool.add_tasks({ADSBSender(clitool.tx_queue, cfg)})
    await clitool.run()


if __name__ == "__main__":
    asyncio.run(main())
