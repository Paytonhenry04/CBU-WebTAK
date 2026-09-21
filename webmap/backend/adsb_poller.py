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
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import adsb_tak  # noqa: E402 - reuse CBU_TAILS/ADSB_URL/fetch_cbu_aircraft

logger = logging.getLogger("adsb_poller")

# uid -> aircraft dict. Read by app.py's /api/aircraft handler.
AIRCRAFT_STATE: dict[str, dict] = {}

# reg -> list of {lat, lon, alt_ft, t} points, in poll order. Cleared each
# time an aircraft transitions from on-ground to airborne, so this is
# effectively "track since takeoff" rather than an unbounded history.
# Read by app.py's /api/aircraft/{reg}/track handler.
TRACK_STATE: dict[str, list[dict]] = {}
MAX_TRACK_POINTS = 1000


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


def _update_track(parsed: dict, was_on_ground: bool) -> None:
    track = TRACK_STATE.setdefault(parsed["reg"], [])
    if was_on_ground and not parsed["on_ground"]:
        track.clear()  # takeoff detected - start a fresh trail
    if not parsed["on_ground"]:
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
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                hits = await adsb_tak.fetch_cbu_aircraft(session)
                for ac in hits:
                    parsed = _to_state(ac)
                    if parsed:
                        prev = AIRCRAFT_STATE.get(parsed["uid"])
                        was_on_ground = prev["on_ground"] if prev else True
                        AIRCRAFT_STATE[parsed["uid"]] = parsed
                        _update_track(parsed, was_on_ground)
                logger.info("Polled %d CBU aircraft", len(hits))
            except Exception as exc:  # noqa: BLE001 - keep polling regardless
                logger.warning("adsb.fi poll failed: %s", exc)
            await asyncio.sleep(adsb_tak.POLL_SECONDS)


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
