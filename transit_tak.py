#!/usr/bin/env python3
"""
transit_tak.py - Poll RTA's (Riverside Transit Agency) GTFS-realtime feed for
Route 1 buses and push them to a TAK server as CoT. Free, keyless, public
feed. Part of the ~/TAK project - mirrors adsb_tak.py's pattern so Route 1
buses on the web map are genuinely TAK-powered, the same as the aircraft.

Sends three kinds of CoT:
  - a self-presence event (required before FTS relays anything from us)
  - one event per live bus, with a custom <bus> detail element
  - a route-level "route_status" heartbeat every poll cycle that succeeds,
    regardless of how many buses were found - this is what lets the listener
    tell "polled fine, zero buses right now (off-hours)" apart from "feed is
    actually down", the same distinction the old direct-poll transit.py used
    to get for free from its own fetched_at timestamp.
"""

import asyncio
import csv
import io
import zipfile
from configparser import ConfigParser
from datetime import datetime, timezone, timedelta
import xml.etree.ElementTree as ET

import aiohttp
import pytak
from google.transit import gtfs_realtime_pb2

import adsb_tak  # reuse iso() and COT_URL

REALTIME_URL = "https://rtabus.com/gtfsrt/vehicles"
STATIC_GTFS_URL = "https://www.riversidetransit.com/google_transit.zip"
ROUTE_SHORT_NAME = "1"

# riversidetransit.com's WAF returns 403 to aiohttp's default UA string even
# though the exact same request succeeds via curl - a real browser-shaped UA
# clears it.
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

POLL_SECONDS = 15                      # RTA's own feed only actually moves a
                                        # vehicle every ~20-25s (confirmed by
                                        # sampling it), but polling at this
                                        # cadence costs nothing on a keyless
                                        # feed and keeps the route-status
                                        # heartbeat prompt.
STATIC_REFRESH_SECONDS = 24 * 60 * 60  # schedules change rarely
STALE_SECONDS = 90                     # per-vehicle CoT stale window

COT_URL = adsb_tak.COT_URL
SELF_UID = "CBU-TRANSIT-FEED"
PRESENCE_SECONDS = 120

# Roughly the middle of Route 1's service area - just a location for this
# feeder's own self-presence/status markers. Each bus carries its own real
# position independently.
PRESENCE_LAT, PRESENCE_LON = 33.9533, -117.3962

_TRIP_LOOKUP: dict[str, dict] = {}
_ROUTE_ID: str | None = None


def make_presence():
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
    point.set("lat", str(PRESENCE_LAT))
    point.set("lon", str(PRESENCE_LON))
    point.set("hae", "0.0")
    point.set("ce", "9999999.0")
    point.set("le", "9999999.0")

    detail = ET.SubElement(event, "detail")
    takv = ET.SubElement(detail, "takv")
    takv.set("os", "linux")
    takv.set("version", "1.0")
    takv.set("device", "transit_tak.py")
    takv.set("platform", "CBU-Transit-Feed")
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


def make_status_cot(vehicle_count: int) -> bytes:
    now = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=STALE_SECONDS)
    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", "CBU-TRANSIT-STATUS")
    event.set("type", "a-f-G-U-C")
    event.set("how", "m-g")
    event.set("time", adsb_tak.iso(now))
    event.set("start", adsb_tak.iso(now))
    event.set("stale", adsb_tak.iso(stale))

    point = ET.SubElement(event, "point")
    point.set("lat", str(PRESENCE_LAT))
    point.set("lon", str(PRESENCE_LON))
    point.set("hae", "0.0")
    point.set("ce", "9999999.0")
    point.set("le", "9999999.0")

    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact").set("callsign", "Route 1 status")
    status = ET.SubElement(detail, "route_status")
    status.set("route", ROUTE_SHORT_NAME)
    status.set("vehicle_count", str(vehicle_count))
    return ET.tostring(event)


def _load_static_gtfs(zip_bytes: bytes) -> None:
    global _ROUTE_ID
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:

        def rows(name):
            with zf.open(name) as f:
                return list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")))

        route_id = next(
            (r["route_id"] for r in rows("routes.txt") if r.get("route_short_name") == ROUTE_SHORT_NAME),
            None,
        )
        if not route_id:
            raise ValueError(f"no route with short_name={ROUTE_SHORT_NAME!r} in static feed")
        _ROUTE_ID = route_id

        trip_lookup = {}
        for t in rows("trips.txt"):
            if t.get("route_id") == route_id:
                trip_lookup[t["trip_id"]] = {
                    "headsign": t.get("trip_headsign") or "",
                    "direction_id": t.get("direction_id") or "",
                    "shape_id": t.get("shape_id") or "",
                }

    _TRIP_LOOKUP.clear()
    _TRIP_LOOKUP.update(trip_lookup)


async def refresh_static(session: aiohttp.ClientSession) -> None:
    async with session.get(
        STATIC_GTFS_URL, headers=REQUEST_HEADERS, timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
        resp.raise_for_status()
        body = await resp.read()
    _load_static_gtfs(body)


async def static_refresh_loop(session: aiohttp.ClientSession, logger) -> None:
    while True:
        await asyncio.sleep(STATIC_REFRESH_SECONDS)
        try:
            await refresh_static(session)
        except Exception as e:  # noqa: BLE001 - keep the old lookup, try again later
            logger.warning("Static GTFS refresh failed (keeping cached lookup): %s", e)


def make_bus_cot(v) -> bytes | None:
    vehicle_id = v.vehicle.id or v.trip.trip_id or None
    if not vehicle_id:
        return None

    now = datetime.now(timezone.utc)
    stale = now + timedelta(seconds=STALE_SECONDS)
    trip_info = _TRIP_LOOKUP.get(v.trip.trip_id, {})

    bearing = v.position.bearing if v.position.HasField("bearing") else None
    speed_ms = v.position.speed if v.position.HasField("speed") else None
    speed_mph = round(speed_ms * 2.23694) if speed_ms is not None else None
    observed_at = (
        datetime.fromtimestamp(v.timestamp, tz=timezone.utc).isoformat() if v.timestamp else ""
    )

    event = ET.Element("event")
    event.set("version", "2.0")
    event.set("uid", f"CBU-BUS-{vehicle_id}")
    event.set("type", "a-f-G-E-V-C")   # ground/friendly/equipment/vehicle/civ - illustrative
    event.set("how", "m-g")
    event.set("time", adsb_tak.iso(now))
    event.set("start", adsb_tak.iso(now))
    event.set("stale", adsb_tak.iso(stale))

    point = ET.SubElement(event, "point")
    point.set("lat", str(v.position.latitude))
    point.set("lon", str(v.position.longitude))
    point.set("hae", "0.0")
    point.set("ce", "9999999.0")
    point.set("le", "9999999.0")

    detail = ET.SubElement(event, "detail")
    ET.SubElement(detail, "contact").set(
        "callsign", trip_info.get("headsign") or f"Route 1 #{vehicle_id}"
    )
    track = ET.SubElement(detail, "track")
    track.set("course", "" if bearing is None else str(bearing))
    track.set("speed", "" if speed_ms is None else str(speed_ms))

    bus = ET.SubElement(detail, "bus")
    bus.set("vehicle_id", vehicle_id)
    bus.set("trip_id", v.trip.trip_id or "")
    bus.set("route", ROUTE_SHORT_NAME)
    bus.set("headsign", trip_info.get("headsign", ""))
    bus.set("direction_id", trip_info.get("direction_id", ""))
    bus.set("shape_id", trip_info.get("shape_id", ""))
    bus.set("bearing", "" if bearing is None else str(bearing))
    bus.set("speed_mph", "" if speed_mph is None else str(speed_mph))
    bus.set("observed_at", observed_at)

    ET.SubElement(detail, "remarks").text = (
        f"RTA Route 1 bus {vehicle_id} | {trip_info.get('headsign', '')}"
    )
    return ET.tostring(event)


class TransitSender(pytak.Worker):
    async def run(self):
        last_presence = 0.0
        async with aiohttp.ClientSession() as session:
            try:
                await refresh_static(session)
                self._logger.info(
                    "Static GTFS loaded: route_id=%s, %d Route 1 trips", _ROUTE_ID, len(_TRIP_LOOKUP)
                )
            except Exception as e:  # noqa: BLE001
                self._logger.warning(
                    "Initial static GTFS fetch failed, continuing without it: %s", e
                )
            asyncio.create_task(static_refresh_loop(session, self._logger))

            watchdog = adsb_tak.TxStallWatchdog(self.queue, self._logger)
            while True:
                watchdog.check()
                try:
                    now = asyncio.get_event_loop().time()
                    if now - last_presence > PRESENCE_SECONDS:
                        await self.put_queue(make_presence())
                        last_presence = now
                        self._logger.info("Announced presence to TAK server")

                    if _ROUTE_ID is None:
                        # Same fail-closed reasoning as the old transit.py:
                        # without a resolved route_id we can't tell Route 1
                        # buses apart from the ~165 other RTA vehicles
                        # system-wide, so skip this cycle entirely rather
                        # than mislabeling all of them.
                        self._logger.warning(
                            "Route 1 route_id not resolved yet, skipping this poll"
                        )
                    else:
                        async with session.get(
                            REALTIME_URL, headers=REQUEST_HEADERS, timeout=aiohttp.ClientTimeout(total=10)
                        ) as resp:
                            resp.raise_for_status()
                            body = await resp.read()
                        feed = gtfs_realtime_pb2.FeedMessage()
                        feed.ParseFromString(body)

                        sent = 0
                        for entity in feed.entity:
                            if not entity.HasField("vehicle"):
                                continue
                            v = entity.vehicle
                            if v.trip.route_id != _ROUTE_ID:
                                continue
                            cot = make_bus_cot(v)
                            if cot:
                                await self.put_queue(cot)
                                sent += 1

                        await self.put_queue(make_status_cot(sent))
                        self._logger.info("Sent %d Route 1 buses to TAK", sent)
                except Exception as e:  # noqa: BLE001 - keep polling regardless
                    self._logger.warning("Transit poll failed: %s", e)
                await asyncio.sleep(POLL_SECONDS)


async def main():
    config = ConfigParser()
    config["transittak"] = {"COT_URL": COT_URL}
    cfg = config["transittak"]
    clitool = pytak.CLITool(cfg)
    await clitool.setup()
    clitool.add_tasks({TransitSender(clitool.tx_queue, cfg)})
    await clitool.run()


if __name__ == "__main__":
    asyncio.run(main())
