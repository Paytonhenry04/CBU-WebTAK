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
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
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


@app.get("/api/aircraft")
async def get_aircraft():
    adsb_poller.prune_stale()
    return JSONResponse(list(adsb_poller.AIRCRAFT_STATE.values()))


@app.get("/api/aircraft/{reg}/track")
async def get_track(reg: str):
    return JSONResponse(adsb_poller.TRACK_STATE.get(reg.upper(), []))


@app.get("/api/health")
async def get_health():
    return {"status": "ok", "tracked_aircraft": len(adsb_poller.AIRCRAFT_STATE)}


app.mount("/static/geo", StaticFiles(directory=DATA_DIR), name="geo")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
