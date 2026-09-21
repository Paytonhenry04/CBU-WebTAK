"""
fts_listener.py - The web map's data source: a real TAK client.

Connects to FreeTAKServer on the CoT port as a registered TAK client and
consumes the aircraft CoT that adsb_tak.py publishes. This is what makes the
web map genuinely TAK-powered rather than a parallel consumer of the same
ADS-B feed - nothing here talks to adsb.fi.

Registration matters: FTS only relays a client's CoT on to other connected
clients once *both* ends have announced themselves with a self-presence CoT
(type a-f-G-U-C). pytak's default hello is a bare 't-x-d-d' ping which FTS
logs as 'takPing' and then errors on. Confirmed by testing - a registered
listener received 0 events from an unregistered sender, and every event once
the sender registered too.
"""

import asyncio
import json
import logging
import os
import sys
import xml.etree.ElementTree as ET
from configparser import ConfigParser
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytak

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import adsb_tak  # noqa: E402 - reuse STALE_SECONDS and the presence format

logger = logging.getLogger("fts_listener")

COT_URL = "tcp://127.0.0.1:8087"
SELF_UID = "CBU-WEBMAP"
PRESENCE_SECONDS = 120
RECONNECT_SECONDS = 5

# uid -> aircraft dict, built purely from CoT received from FreeTAKServer.
AIRCRAFT_STATE: dict[str, dict] = {}

# reg -> list of {lat, lon, alt_ft, t}. Cleared on an observed on-ground ->
# airborne transition so a trail runs from the runway to the current position.
TRACK_STATE: dict[str, list[dict]] = {}
MAX_TRACK_POINTS = 5000

TRACK_FILE = Path(__file__).resolve().parent.parent / "track_history.json"
TRACK_RETENTION_HOURS = 12

# When the last CoT arrived, so the UI can be honest about the TAK link
# instead of just showing an empty map.
LINK_STATE: dict[str, object] = {"connected": False, "last_cot": None}


def _self_presence() -> bytes:
    """Announce ourselves so FTS routes other clients' CoT to us."""
    now = datetime.now(timezone.utc)
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", SELF_UID)
    event.set("type", "a-f-G-U-C")
    event.set("how", "m-g")
    event.set("time", adsb_tak.iso(now))
    event.set("start", adsb_tak.iso(now))
    event.set("stale", adsb_tak.iso(now + timedelta(seconds=PRESENCE_SECONDS * 2)))

    point = ET.SubElement(event, "point")
    point.set("lat", str(adsb_tak.KRAL_LAT))
    point.set("lon", str(adsb_tak.KRAL_LON))
    point.set("hae", "0.0")
    point.set("ce", "9999999.0")
    point.set("le", "9999999.0")

    detail = ET.SubElement(event, "detail")
    takv = ET.SubElement(detail, "takv")
    takv.set("os", "linux")
    takv.set("version", "1.0")
    takv.set("device", "webmap")
    takv.set("platform", "CBU-WebMap")
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


def _parse_cot(data: bytes) -> dict | None:
    """Turn one relayed CoT event into our aircraft dict, or None if it isn't
    a CBU aircraft event (chat, presence, pings, other clients)."""
    try:
        event = ET.fromstring(data)
    except ET.ParseError:
        return None

    uid = event.get("uid", "")
    point = event.find("point")
    detail = event.find("detail")
    if point is None or detail is None:
        return None

    # Require the <aircraft> detail element rather than filtering on the uid
    # prefix: our own presence CoT is uid "CBU-WEBMAP", which a prefix check
    # would happily accept and render as a phantom aircraft.
    aircraft_el = detail.find("aircraft")
    if aircraft_el is None or uid in (SELF_UID, adsb_tak.SELF_UID):
        return None
    lat, lon = point.get("lat"), point.get("lon")
    if lat is None or lon is None:
        return None

    try:
        hae = float(point.get("hae") or 0)
    except ValueError:
        hae = 0.0

    course = speed_ms = 0.0
    track_el = detail.find("track")
    if track_el is not None:
        try:
            course = float(track_el.get("course") or 0)
            speed_ms = float(track_el.get("speed") or 0)
        except ValueError:
            pass

    reg = aircraft_el.get("reg") or None
    ac_type = aircraft_el.get("type") or None
    owner = aircraft_el.get("owner") or None
    hex_id = aircraft_el.get("hex") or None
    on_ground = (aircraft_el.get("ground") or "").lower() == "true"

    reg = reg or uid.removeprefix("CBU-")

    callsign = None
    contact_el = detail.find("contact")
    if contact_el is not None:
        callsign = contact_el.get("callsign")

    return {
        "uid": uid,
        "reg": reg,
        "callsign": callsign or reg,
        "type": ac_type,
        "owner": owner,
        "hex": hex_id,
        "lat": float(lat),
        "lon": float(lon),
        "alt_ft": round(hae / 0.3048),
        "speed_kt": round(speed_ms / 0.514444),
        "heading": course,
        "on_ground": on_ground,
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }


def load_tracks() -> None:
    if not TRACK_FILE.exists():
        return
    try:
        stored = json.loads(TRACK_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s (%s), starting empty", TRACK_FILE, exc)
        return
    cutoff = datetime.now(timezone.utc) - timedelta(hours=TRACK_RETENTION_HOURS)
    restored = 0
    for reg, points in stored.items():
        if not points:
            continue
        try:
            if datetime.fromisoformat(points[-1]["t"]) < cutoff:
                continue
        except (KeyError, ValueError):
            continue
        TRACK_STATE[reg] = points[-MAX_TRACK_POINTS:]
        restored += 1
    logger.info("Restored %d flight trails from %s", restored, TRACK_FILE)


def save_tracks() -> None:
    try:
        tmp = TRACK_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(TRACK_STATE))
        os.replace(tmp, TRACK_FILE)
    except OSError as exc:
        logger.warning("Could not persist trails: %s", exc)


def _update_track(parsed: dict, was_on_ground: bool) -> None:
    """While on the ground only the latest fix is kept; at liftoff that fix is
    retained as the trail's origin so the line starts at the runway."""
    track = TRACK_STATE.setdefault(parsed["reg"], [])
    if was_on_ground and not parsed["on_ground"]:
        del track[:-1]
    elif parsed["on_ground"]:
        track.clear()
    track.append(
        {
            "lat": parsed["lat"],
            "lon": parsed["lon"],
            "alt_ft": parsed["alt_ft"],
            "t": parsed["last_seen"],
        }
    )
    del track[:-MAX_TRACK_POINTS]


def prune_stale() -> None:
    now = datetime.now(timezone.utc)
    stale = [
        uid
        for uid, ac in AIRCRAFT_STATE.items()
        if (now - datetime.fromisoformat(ac["last_seen"])).total_seconds()
        > adsb_tak.STALE_SECONDS
    ]
    for uid in stale:
        del AIRCRAFT_STATE[uid]


async def _drain(rx_queue: asyncio.Queue) -> None:
    while True:
        data = await rx_queue.get()
        LINK_STATE["last_cot"] = datetime.now(timezone.utc).isoformat()
        parsed = _parse_cot(data)
        if not parsed:
            continue
        prev = AIRCRAFT_STATE.get(parsed["uid"])
        # Default False, not True: with no prior observation (first sight or
        # first event after a restart) we haven't *seen* it on the ground, so
        # calling it a takeoff would wipe the trail restored from disk.
        was_on_ground = prev["on_ground"] if prev else False
        AIRCRAFT_STATE[parsed["uid"]] = parsed
        _update_track(parsed, was_on_ground)


async def _save_loop() -> None:
    while True:
        await asyncio.sleep(10)
        if TRACK_STATE:
            save_tracks()


async def _presence_loop(tx_queue: asyncio.Queue) -> None:
    while True:
        await tx_queue.put(_self_presence())
        await asyncio.sleep(PRESENCE_SECONDS)


async def _connect_once() -> None:
    config = ConfigParser()
    config["webmap"] = {"COT_URL": COT_URL, "PYTAK_NO_HELLO": "1"}
    clitool = pytak.CLITool(config["webmap"])
    await clitool.setup()

    drain = asyncio.create_task(_drain(clitool.rx_queue))
    presence = asyncio.create_task(_presence_loop(clitool.tx_queue))
    saver = asyncio.create_task(_save_loop())
    LINK_STATE["connected"] = True
    logger.info("Connected to FreeTAKServer at %s as %s", COT_URL, SELF_UID)
    try:
        await clitool.run()
    finally:
        LINK_STATE["connected"] = False
        for task in (drain, presence, saver):
            task.cancel()
        save_tracks()


async def run_forever() -> None:
    load_tracks()
    while True:
        try:
            await _connect_once()
        except Exception as exc:  # noqa: BLE001 - this is the reconnect loop
            logger.warning("TAK link lost (%s), reconnecting in %ss", exc, RECONNECT_SECONDS)
        await asyncio.sleep(RECONNECT_SECONDS)
