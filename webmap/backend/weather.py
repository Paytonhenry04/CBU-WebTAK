"""
weather.py - Live KRAL weather, sourced purely from TAK.

weather_tak.py (project root) polls NOAA's Aviation Weather Center and
publishes it as CoT to FreeTAKServer; fts_listener.py (this backend's own TAK
client) receives that CoT and populates WEATHER_STATE. This module just
shapes that state into the same response /api/weather always returned -
nothing here talks to NOAA directly, matching the project's rule that any
data reaching the web map has to come through TAK, the same as the aircraft.
"""

from datetime import datetime, timezone

import fts_listener

STALE_AFTER_SECONDS = 90 * 60  # matches weather_tak.py's 5-min poll cadence, plus buffer


def get_state() -> dict:
    """Snapshot of the CoT-derived weather state, with staleness computed
    lazily at read time from when we last actually received a weather CoT."""
    state = dict(fts_listener.WEATHER_STATE)
    last_seen = state.get("last_seen")
    available = last_seen is not None
    if last_seen is None:
        stale = False  # nothing received yet - "unavailable", not "stale"
    else:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last_seen)).total_seconds()
        stale = age > STALE_AFTER_SECONDS

    return {
        "icao": state.get("icao") or "KRAL",
        "available": available,
        "stale": stale,
        "flight_category": state.get("flight_category"),
        "wind": state.get("wind"),
        "visibility_sm": state.get("visibility_sm"),
        "temp_c": state.get("temp_c"),
        "dewpoint_c": state.get("dewpoint_c"),
        "altimeter_inhg": state.get("altimeter_inhg"),
        "raw_metar": state.get("raw_metar"),
        "observed_at": state.get("observed_at"),
        "fetched_at": last_seen,
        # No longer meaningful here - a failed upstream METAR fetch on the
        # feeder side just means no new CoT arrives, which shows up as
        # staleness rather than a per-request error message. See
        # weather_tak.py's own log for actual fetch failures.
        "error": None,
    }
