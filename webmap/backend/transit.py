"""
transit.py - Route 1 stops/shapes plus live Route 1 bus positions.

Stops and shapes are static reference geometry (Route 1's own stop_times.txt/
shapes.txt from RTA's published GTFS schedule) - fetched directly, once, the
same way this app already handles CBU's building footprints and the KRAL
airport boundary. That's geography, not live telemetry, so it isn't routed
through TAK.

Live bus positions are different: they come purely from TAK now.
transit_tak.py (project root) polls RTA's realtime feed and publishes each
bus as CoT to FreeTAKServer; fts_listener.py (this backend's own TAK client)
receives that CoT and populates BUS_STATE/TRANSIT_LINK_STATE. This module
just shapes that state into the same response /api/transit/route1 always
returned - nothing here polls RTA's realtime feed directly anymore.
"""

import asyncio
import csv
import io
import logging
import zipfile
from datetime import datetime, timezone

import aiohttp

import fts_listener

logger = logging.getLogger("transit")

STATIC_GTFS_URL = "https://www.riversidetransit.com/google_transit.zip"
ROUTE_SHORT_NAME = "1"

# riversidetransit.com's WAF returns 403 to aiohttp's default UA string even
# though the exact same request succeeds via curl - a real browser-shaped UA
# clears it.
REQUEST_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}

STATIC_REFRESH_SECONDS = 24 * 60 * 60  # schedules change rarely
STALE_AFTER_SECONDS = 90               # matches transit_tak.py's route-status
                                        # heartbeat cadence (sent every ~15s
                                        # poll cycle), plus buffer.

_STOPS: list[dict] = []
_SHAPES: dict[str, list[tuple[float, float]]] = {}
_ROUTE_ID: str | None = None


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

        route1_trip_ids = set()
        route1_shape_ids = set()
        for t in rows("trips.txt"):
            if t.get("route_id") == route_id:
                route1_trip_ids.add(t["trip_id"])
                if t.get("shape_id"):
                    route1_shape_ids.add(t["shape_id"])

        route1_stop_ids = set()
        for st in rows("stop_times.txt"):
            if st.get("trip_id") in route1_trip_ids:
                route1_stop_ids.add(st.get("stop_id"))

        stops = []
        for s in rows("stops.txt"):
            if s.get("stop_id") in route1_stop_ids:
                try:
                    lat, lon = float(s["stop_lat"]), float(s["stop_lon"])
                except (KeyError, ValueError):
                    continue
                stops.append(
                    {"id": s["stop_id"], "name": s.get("stop_name"), "lat": lat, "lon": lon}
                )

        # RTA publishes a handful of shape_ids per route (both directions
        # plus short-turn variants), not one - so this keeps all of them
        # rather than guessing which is "the" Route 1 path.
        shapes: dict[str, list[tuple[float, float]]] = {}
        if "shapes.txt" in zf.namelist() and route1_shape_ids:
            points_by_shape: dict[str, list[tuple[int, float, float]]] = {}
            for s in rows("shapes.txt"):
                sid = s.get("shape_id")
                if sid not in route1_shape_ids:
                    continue
                try:
                    seq = int(s["shape_pt_sequence"])
                    lat, lon = float(s["shape_pt_lat"]), float(s["shape_pt_lon"])
                except (KeyError, ValueError):
                    continue
                points_by_shape.setdefault(sid, []).append((seq, lat, lon))
            shapes = {
                sid: [(lat, lon) for _, lat, lon in sorted(pts)]
                for sid, pts in points_by_shape.items()
            }

    _STOPS.clear()
    _STOPS.extend(stops)
    _SHAPES.clear()
    _SHAPES.update(shapes)
    logger.info(
        "Static GTFS loaded: route_id=%s, %d stops, %d shapes", _ROUTE_ID, len(stops), len(shapes)
    )


async def _refresh_static(session: aiohttp.ClientSession) -> None:
    async with session.get(
        STATIC_GTFS_URL, headers=REQUEST_HEADERS, timeout=aiohttp.ClientTimeout(total=30)
    ) as resp:
        resp.raise_for_status()
        body = await resp.read()
    _load_static_gtfs(body)


async def run_forever() -> None:
    """Keeps Route 1's static stops/shapes fresh. No realtime polling here
    anymore - that's transit_tak.py's job now, via TAK."""
    async with aiohttp.ClientSession() as session:
        try:
            await _refresh_static(session)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Initial static GTFS fetch failed, continuing without it: %s", exc)
        while True:
            await asyncio.sleep(STATIC_REFRESH_SECONDS)
            try:
                await _refresh_static(session)
            except Exception as exc:  # noqa: BLE001 - keep the old lookup, try again later
                logger.warning("Static GTFS refresh failed (keeping cached lookup): %s", exc)


def get_stops() -> list[dict]:
    return list(_STOPS)


def get_shapes() -> dict:
    """Route 1's real road path(s) as GeoJSON LineStrings, one per shape_id."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"shape_id": sid},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lat, lon in pts],
                },
            }
            for sid, pts in _SHAPES.items()
        ],
    }


def get_state() -> dict:
    """Snapshot of the CoT-derived live bus state. `fetched_at`/`stale` track
    transit_tak.py's route-status heartbeat (sent every successful poll
    cycle regardless of vehicle count) rather than individual bus CoT, so a
    genuinely empty Route 1 (off-hours) reads as healthy-but-empty instead of
    stale - the same distinction the old direct-poll version got for free
    from its own fetched_at timestamp."""
    last_seen = fts_listener.TRANSIT_LINK_STATE.get("last_seen")
    available = last_seen is not None
    if last_seen is None:
        stale = False
    else:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_seen)).total_seconds()
        stale = age > STALE_AFTER_SECONDS

    vehicles = [
        {k: v for k, v in b.items() if k != "last_seen"}
        for b in fts_listener.BUS_STATE.values()
    ]
    return {
        "available": available,
        "stale": stale,
        "error": None,
        "fetched_at": last_seen,
        "vehicle_count": len(vehicles),
        "vehicles": vehicles,
    }
