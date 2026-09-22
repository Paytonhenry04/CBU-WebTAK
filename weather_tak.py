#!/usr/bin/env python3
"""
weather_tak.py - Poll NOAA's Aviation Weather Center for KRAL and push it to
a TAK server as CoT. Free, keyless, public API. Part of the ~/TAK project -
mirrors adsb_tak.py's pattern exactly, so the web map's weather widget is
genuinely TAK-powered instead of a second thing polling a public API on the
side of the TAK-fed plane data.
"""

import asyncio
from configparser import ConfigParser
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

import aiohttp
import pytak

import adsb_tak  # reuse iso(), KRAL_LAT/KRAL_LON, COT_URL

ICAO = "KRAL"
METAR_URL = f"https://aviationweather.gov/api/data/metar?ids={ICAO}&format=json"

POLL_SECONDS = 300          # 5 min: METAR updates ~hourly except specials;
                             # this catches those promptly at a trivial,
                             # courteous load (12 req/hr) on a free API.
STALE_SECONDS = 90 * 60     # one missed hourly cycle, plus buffer.

COT_URL = adsb_tak.COT_URL
SELF_UID = "CBU-WEATHER-FEED"
PRESENCE_SECONDS = 120


def make_presence():
    """Self-presence CoT - see adsb_tak.py's make_presence() for why this is
    required before FTS will relay anything from this client at all."""
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
    takv.set("device", "weather_tak.py")
    takv.set("platform", "CBU-Weather-Feed")
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


def _parse_wind(wdir, wspd):
    speed_kt = int(wspd) if wspd is not None else None
    if wdir == "VRB":
        return None, True, speed_kt
    try:
        dir_deg = int(wdir) if wdir is not None else None
    except (TypeError, ValueError):
        dir_deg = None
    return dir_deg, False, speed_kt


def make_cot(m: dict) -> bytes:
    """Turn one METAR observation into a CoT XML bytestring."""
    now = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=STALE_SECONDS)

    dir_deg, variable, speed_kt = _parse_wind(m.get("wdir"), m.get("wspd"))
    altim = m.get("altim")
    altimeter_inhg = round(altim * 0.0295299830714, 2) if altim is not None else None
    obs_time = m.get("obsTime")
    observed_at = (
        datetime.fromtimestamp(obs_time, tz=timezone.utc).isoformat()
        if obs_time is not None
        else ""
    )

    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", f"CBU-WEATHER-{ICAO}")
    event.set("type", "a-f-G-U-C")
    event.set("how", "m-g")
    event.set("time", adsb_tak.iso(now))
    event.set("start", adsb_tak.iso(now))
    event.set("stale", adsb_tak.iso(stale))

    point = ET.SubElement(event, "point")
    point.set("lat", str(adsb_tak.KRAL_LAT))
    point.set("lon", str(adsb_tak.KRAL_LON))
    point.set("hae", "0.0")
    point.set("ce", "9999999.0")
    point.set("le", "9999999.0")

    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact").set("callsign", f"{ICAO} weather")

    # Custom, spec-legal detail child - same pattern as adsb_tak.py's
    # <aircraft> element: real TAK clients ignore unknown detail elements,
    # our own webmap backend reads this back out of FTS.
    weather = ET.SubElement(detail, "weather")
    weather.set("icao", ICAO)
    weather.set("flight_category", m.get("fltCat") or "")
    weather.set("wind_dir_deg", "" if dir_deg is None else str(dir_deg))
    weather.set("wind_variable", "true" if variable else "false")
    weather.set("wind_speed_kt", "" if speed_kt is None else str(speed_kt))
    weather.set("visibility_sm", "" if m.get("visib") is None else str(m.get("visib")))
    weather.set("temp_c", "" if m.get("temp") is None else str(m.get("temp")))
    weather.set("dewpoint_c", "" if m.get("dewp") is None else str(m.get("dewp")))
    weather.set("altimeter_inhg", "" if altimeter_inhg is None else str(altimeter_inhg))
    weather.set("observed_at", observed_at)
    weather.set("raw_metar", m.get("rawOb") or "")

    remarks = f"{ICAO} {m.get('fltCat') or '?'} | {m.get('rawOb') or ''}"
    ET.SubElement(detail, "remarks").text = remarks
    return ET.tostring(event)


async def fetch_metar(session: aiohttp.ClientSession) -> dict:
    async with session.get(METAR_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        data = await resp.json()
    if not data:
        raise ValueError(f"{ICAO} missing from METAR response")
    return data[0]


class WeatherSender(pytak.Worker):
    async def run(self):
        last_presence = 0.0
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    now = asyncio.get_event_loop().time()
                    if now - last_presence > PRESENCE_SECONDS:
                        await self.put_queue(make_presence())
                        last_presence = now
                        self._logger.info("Announced presence to TAK server")

                    metar = await fetch_metar(session)
                    await self.put_queue(make_cot(metar))
                    self._logger.info("Sent %s METAR to TAK: %s", ICAO, metar.get("fltCat"))
                except Exception as e:  # noqa: BLE001 - keep polling regardless
                    self._logger.warning("METAR poll failed: %s", e)
                await asyncio.sleep(POLL_SECONDS)


async def main():
    config = ConfigParser()
    config["weathertak"] = {"COT_URL": COT_URL}
    cfg = config["weathertak"]
    clitool = pytak.CLITool(cfg)
    await clitool.setup()
    clitool.add_tasks({WeatherSender(clitool.tx_queue, cfg)})
    await clitool.run()


if __name__ == "__main__":
    asyncio.run(main())
