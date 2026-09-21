"""
adsb_poller.py - Polls adsb.fi directly for CBU aircraft, the same
proven-reliable approach adsb_tak.py uses.

FTS's CoT relay-to-other-connected-clients turned out to be unreliable in
testing (see webmap/README.md), so the web map gets its data straight from
adsb.fi rather than reading it back out of FTS. adsb_tak.py's own CoT feed
into FTS - including the richer type/owner/hex detail it now embeds - is
unaffected; real TAK clients (ATAK etc.) still get that.
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import adsb_tak  # noqa: E402 - reuse CBU_TAILS/ADSB_URL/fetch_cbu_aircraft

logger = logging.getLogger("adsb_poller")

# uid -> aircraft dict. Read by app.py's /api/aircraft handler.
AIRCRAFT_STATE: dict[str, dict] = {}

# reg -> list of {lat, lon, alt_ft, t} points, in poll order. Cleared only on
# an observed on-ground -> airborne transition, so a trail represents the
# flight since takeoff. Read by app.py's /api/aircraft/{reg}/track handler.
TRACK_STATE: dict[str, list[dict]] = {}
MAX_TRACK_POINTS = 5000

# Trails are persisted to disk so a backend restart doesn't wipe a flight's
# history mid-flight (there is no free historical-trace API to rebuild it
# from - adsb.fi's is Cloudflare-gated, adsb.lol doesn't serve traces - so
# once a point is lost it's gone for good).
TRACK_FILE = Path(__file__).resolve().parent.parent / "track_history.json"
TRACK_RETENTION_HOURS = 12

# Deliberately separate from adsb_tak.POLL_SECONDS: the web map wants
# smoother movement than the CoT feeder needs, and changing the shared
# constant would alter what adsb_tak.py sends to FreeTAKServer too.
POLL_SECONDS = 3


def _to_state(ac: dict) -> dict | None:
    reg = ac.get("r", "UNKNOWN")
    lat, lon = ac.get("lat"), ac.get("lon")
    if lat is None or lon is None:
        return None

    alt_baro = ac.get("alt_baro", 0)
    on_ground = alt_baro == "ground"
    alt_ft = 0 if on_ground else float(alt_baro or 0)
    gs_kt = float(ac.get("gs", 0) or 0)
    course = float(ac.get("track", 0) or 0)
    callsign = (ac.get("flight") or reg).strip()

    return {
        "uid": f"CBU-{reg}",
        "reg": reg,
        "callsign": callsign,
        "type": (ac.get("t") or "").strip() or None,
        "owner": (ac.get("ownOp") or "").strip() or None,
        "hex": (ac.get("hex") or "").strip() or None,
        "lat": float(lat),
        "lon": float(lon),
        "alt_ft": round(alt_ft),
        "speed_kt": round(gs_kt),
        "heading": course,
        "on_ground": on_ground,
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }


def load_tracks() -> None:
    """Restore trails from disk on startup, dropping anything too old."""
    if not TRACK_FILE.exists():
        return
    try:
        stored = json.loads(TRACK_FILE.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s (%s), starting with empty trails", TRACK_FILE, exc)
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
    """Write trails to disk atomically (temp file + rename), so a crash or
    restart mid-write can't leave a corrupted history file behind."""
    try:
        tmp = TRACK_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(TRACK_STATE))
        os.replace(tmp, TRACK_FILE)
    except OSError as exc:
        logger.warning("Could not persist trails to %s: %s", TRACK_FILE, exc)


def _update_track(parsed: dict, was_on_ground: bool) -> None:
    """Maintain a trail that runs from the departure point to the aircraft's
    current position.

    While an aircraft is on the ground only its latest fix is kept, so a long
    taxi doesn't clutter the trail. The moment it leaves the ground that last
    ground fix is retained as the trail's origin, which is what makes the
    line actually start at the runway rather than at the first airborne fix.

    Note: touch-and-go circuits (common for these training aircraft) briefly
    register as on-ground, so each circuit starts a fresh trail.
    """
    track = TRACK_STATE.setdefault(parsed["reg"], [])
    on_ground = parsed["on_ground"]

    if was_on_ground and not on_ground:
        del track[:-1]  # just lifted off - keep the departure fix as origin
    elif on_ground:
        track.clear()  # on the ground - hold only the latest fix

    track.append(
        {
            "lat": parsed["lat"],
            "lon": parsed["lon"],
            "alt_ft": parsed["alt_ft"],
            "t": parsed["last_seen"],
        }
    )
    del track[:-MAX_TRACK_POINTS]


async def run_forever() -> None:
    load_tracks()
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                hits = await adsb_tak.fetch_cbu_aircraft(session)
                for ac in hits:
                    parsed = _to_state(ac)
                    if parsed:
                        prev = AIRCRAFT_STATE.get(parsed["uid"])
                        # Default False, NOT True: with no previous
                        # observation (first sight, or first poll after a
                        # restart) we have not *seen* this aircraft on the
                        # ground, so treating it as a takeoff would wipe the
                        # trail we just restored from disk mid-flight.
                        was_on_ground = prev["on_ground"] if prev else False
                        AIRCRAFT_STATE[parsed["uid"]] = parsed
                        _update_track(parsed, was_on_ground)
                if hits:
                    save_tracks()
                logger.info("Polled %d CBU aircraft", len(hits))
            except Exception as exc:  # noqa: BLE001 - keep polling regardless
                logger.warning("adsb.fi poll failed: %s", exc)
            await asyncio.sleep(POLL_SECONDS)


def prune_stale() -> None:
    now = datetime.now(timezone.utc)
    stale_uids = [
        uid
        for uid, ac in AIRCRAFT_STATE.items()
        if (now - datetime.fromisoformat(ac["last_seen"])).total_seconds()
        > adsb_tak.STALE_SECONDS
    ]
    for uid in stale_uids:
        del AIRCRAFT_STATE[uid]
