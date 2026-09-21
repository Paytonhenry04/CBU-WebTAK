#!/usr/bin/env python3
"""
adsb_tak.py - Poll ADS-B for CBU aircraft and push them to a TAK server as CoT.
Public data only (airplanes.live). Part of the ~/TAK project.
"""

import asyncio
from configparser import ConfigParser
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

import aiohttp
import pytak

# CBU fleet tail numbers (from CBU's public VA aviation docs)
CBU_TAILS = {
    "N935CB", "N944CB", "N207CB", "N233CB",
    "N255CB", "N246CB", "N248CB", "N259CB",
    "N374CB", "N249CB", "N55462",

}

# Riverside Municipal Airport (KRAL) - CBU flies out of here
KRAL_LAT = 33.9519
KRAL_LON = -117.4459
SEARCH_RADIUS_NM = 100      # how far around KRAL to look
POLL_SECONDS = 10          # how often to poll ADS-B
STALE_SECONDS = 60         # how long a marker lives without an update

# Free, no-auth ADS-B feed. Verify at https://airplanes.live/rest-api-adsb/
ADSB_URL = f"https://opendata.adsb.fi/api/v2/lat/{KRAL_LAT}/lon/{KRAL_LON}/dist/{SEARCH_RADIUS_NM}"

# Where to send CoT. Local FreeTAKServer on this box = tcp://127.0.0.1:8087
COT_URL = "tcp://127.0.0.1:8087"


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def make_cot(ac):
    """Turn one aircraft dict into a CoT XML bytestring."""
    now = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=STALE_SECONDS)

    reg = ac.get("r", "UNKNOWN")
    lat = ac.get("lat")
    lon = ac.get("lon")
    if lat is None or lon is None:
        return None

    alt_baro = ac.get("alt_baro", 0)
    alt_ft = 0 if alt_baro == "ground" else float(alt_baro or 0)
    hae = alt_ft * 0.3048

    gs_kt = float(ac.get("gs", 0) or 0)
    speed_ms = gs_kt * 0.514444
    course = float(ac.get("track", 0) or 0)
    callsign = (ac.get("flight") or reg).strip()

    ac_type = (ac.get("t") or "").strip()
    owner = (ac.get("ownOp") or "").strip()
    hex_id = (ac.get("hex") or "").strip()

    event = ET.Element("event")
    event.set("version", "2.0")
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

    remarks = f"CBU aircraft {reg} | alt {int(alt_ft)} ft | gs {int(gs_kt)} kt"
    if ac_type:
        remarks += f" | type {ac_type}"
    if owner:
        remarks += f" | owner {owner}"
    ET.SubElement(detail, "remarks").text = remarks
    return ET.tostring(event)


async def fetch_cbu_aircraft(session):
    """Fetch aircraft near KRAL, keep only CBU tails."""
    headers = {"User-Agent": "cbu-tak-demo"}
    async with session.get(ADSB_URL, headers=headers, timeout=15) as resp:
        data = await resp.json()
    aircraft = data.get("aircraft") or data.get("ac", [])
    return [a for a in aircraft if (a.get("r") or "").upper() in CBU_TAILS]


class ADSBSender(pytak.Worker):
    async def run(self):
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    hits = await fetch_cbu_aircraft(session)
                    for ac in hits:
                        cot = make_cot(ac)
                        if cot:
                            await self.put_queue(cot)
                    self._logger.info("Sent %d CBU aircraft to TAK", len(hits))
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
