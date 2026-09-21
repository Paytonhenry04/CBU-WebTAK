"""
app.py - FastAPI backend for the CBU/KRAL live webmap.

Serves the MapLibre frontend, the cached OSM GeoJSON, and a polling-friendly
/api/aircraft endpoint fed by adsb_poller.py (which polls adsb.fi directly -
see webmap/README.md for why this doesn't read the data back out of FTS).
The endpoint itself is deliberately polling-based, not WebSockets - see
PROJECT_STATUS.md for why: WebSocket upgrades through this project's home
router were never confirmed reliable, while plain HTTP polling was proven
reliable end-to-end (LAN and genuinely external) every time it was tested.
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import adsb_poller

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webmap")

WEBMAP_ROOT = Path(__file__).parent.parent
FRONTEND_DIR = WEBMAP_ROOT / "frontend"
DATA_DIR = WEBMAP_ROOT / "data"

PRUNE_INTERVAL_SECONDS = 10


async def _prune_loop():
    while True:
        adsb_poller.prune_stale()
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI):
    poller_task = asyncio.create_task(adsb_poller.run_forever())
    prune_task = asyncio.create_task(_prune_loop())
    logger.info("Started adsb.fi poller and stale-aircraft pruner")
    try:
        yield
    finally:
        poller_task.cancel()
        prune_task.cancel()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def no_cache(request: Request, call_next):
    # StaticFiles sends no Cache-Control by default, so browsers fall back
    # to heuristic caching and can silently keep serving an old app.js
    # across normal refreshes while this project is actively iterating.
    # force revalidation (still cheap: ETag/Last-Modified still give 304s).
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/api/aircraft")
async def get_aircraft():
    adsb_poller.prune_stale()
    return JSONResponse(list(adsb_poller.AIRCRAFT_STATE.values()))


@app.get("/api/aircraft/{reg}/track")
async def get_track(reg: str):
    return JSONResponse(adsb_poller.TRACK_STATE.get(reg.upper(), []))


@app.get("/api/buildings")
async def get_buildings():
    """CBU building footprints with local name/description overrides applied.

    Both files are re-read per request (they're small), so editing
    data/building_info.json takes effect on a page reload - no need to
    re-run the Overpass fetch, which is slow and occasionally 504s.
    """
    buildings = json.loads((DATA_DIR / "cbu_buildings.geojson").read_text())

    overrides = {}
    info_path = DATA_DIR / "building_info.json"
    if info_path.exists():
        try:
            overrides = {
                key: value
                for key, value in json.loads(info_path.read_text()).items()
                if not key.startswith("_")
            }
        except json.JSONDecodeError as exc:
            logger.warning("building_info.json is not valid JSON (%s), ignoring", exc)

    applied = 0
    for feature in buildings.get("features", []):
        props = feature.get("properties", {})
        override = overrides.get(str(props.get("osm_id")))
        if not override:
            continue
        if override.get("name"):
            props["name"] = override["name"]
        if override.get("description"):
            props["description"] = override["description"]
        applied += 1

    buildings["overrides_applied"] = applied
    return JSONResponse(buildings)


@app.get("/api/health")
async def get_health():
    return {"status": "ok", "tracked_aircraft": len(adsb_poller.AIRCRAFT_STATE)}


app.mount("/static/geo", StaticFiles(directory=DATA_DIR), name="geo")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
