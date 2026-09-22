# CBU/KRAL Live Web Map

Publicly-accessible live map of CBU's tracked aircraft, **fed from
FreeTAKServer over the TAK protocol**. Runs alongside `adsb_tak.py` and the
`ftserver/` Docker stack.

## Data flow

```
   adsb.fi
      |
      | poll
      v
 adsb_tak.py  --- CoT/TCP --->  FreeTAKServer (:8087)
 (registers as                        |
  a TAK client)                       | relays CoT
                                      v
                            fts_listener.py  (registers as a TAK client)
                                      |
                              AIRCRAFT_STATE / TRACK_STATE
                                      |
                            GET /api/aircraft  (polled by browser)
                                      |
                                      v
                     frontend/ (MapLibre GL JS) renders it
```

Aircraft data reaches the web map **only** as CoT from FreeTAKServer -
nothing in the backend talks to adsb.fi. Verified by killing `adsb_tak.py`
and watching the map drop to zero aircraft rather than silently falling
back to a direct feed.

Everything survives the TAK round-trip: registration, callsign, position,
altitude, speed, heading, aircraft type, owner, ICAO hex and the
on-ground flag, carried in a custom `<aircraft>` element inside the CoT
`<detail>` block. FTS restructures events slightly in transit (remarks
become an attribute, and it adds its own `<marti>`/`<usericon>`), but the
custom element passes through intact.

## Both ends must register as TAK clients

This is the thing that makes the relay work, and it cost a lot of
debugging to find. FreeTAKServer only forwards a client's CoT on to *other*
connected clients once that client has announced itself with a
self-presence CoT (`type="a-f-G-U-C"`, with `<takv>`, `<contact>`, `<uid>`
and `<__group>`). pytak's default hello is a bare `t-x-d-d` ping, which FTS
logs as `takPing` and then throws an exception on.

Measured directly:

- unregistered sender -> registered listener: **0 events received**
- registered sender -> registered listener: **every event received**

So `adsb_tak.py` and `fts_listener.py` both send a self-presence CoT on
connect and re-announce every 2 minutes. Earlier attempts at this failed
and were wrongly attributed to connection ordering; registration was the
actual cause.

If the TAK link drops or no CoT arrives, the UI says so explicitly rather
than showing an empty map that looks like "no flights". `/api/health`
reports `tak_connected` and `seconds_since_last_cot`.

## Running it


```bash
# one-time: fetch/refresh cached OSM building + airport geometry
cd webmap/data && ../../venv/bin/python3 fetch_osm_data.py

# start the backend (serves the frontend + /api/aircraft)
cd webmap/backend
../../venv/bin/uvicorn app:app --host 0.0.0.0 --port 8090
```

`adsb_tak.py` and the `ftserver/` stack must both be running - they are the
web map's only data source now. If `adsb_tak.py` isn't up, the map shows
"TAK connected - no CoT received" rather than an empty map. Start order
doesn't matter: both ends re-announce their presence every 2 minutes and
the listener reconnects on its own.

## Refreshing the OSM cache

`webmap/data/cbu_buildings.geojson` and `kral_airport.geojson` are static,
fetched once via the Overpass API and committed as plain files - nothing at
runtime hits Overpass. Re-run `fetch_osm_data.py` manually if the campus
adds buildings or the cached data otherwise goes stale. The Overpass public
instance is occasionally slow/returns a transient 504 - just retry.

## Naming buildings / adding descriptions

Only 76 of the 223 cached CBU building footprints have a `name` in
OpenStreetMap, and OSM has no field for free-form descriptions. All of it
is controlled locally via `data/building_info.json`, keyed by OSM id:

```json
{
  "233663002": {
    "name": "Engineering Building",
    "type": "Academic",
    "description": "Houses the aviation program's flight simulators."
  }
}
```

The file ships pre-populated with an entry for **every** building, filled
in with whatever OSM already knows and a blank description to write
yourself - named buildings first (alphabetically), then the 147 unnamed
ones. Regenerate it with:

```bash
cd webmap/data && ../../venv/bin/python3 seed_building_info.py
```

That is safe to re-run: existing entries are preserved verbatim, so your
edits are never overwritten; only buildings missing from the file are
added. Run it again after re-fetching OSM data to pick up new footprints.

Workflow: click a building on the map - the popup shows its `osm_id` -
then edit that entry. Buildings are colour-coded by how complete they are:
**black** = no name yet, **red** = named but no description, **yellow** =
name and description both done. Buildings around campus that aren't in our
data are drawn by the base map style in its own grey and are left alone.

All three fields are optional and apply to named and unnamed buildings
alike. A **blank field means "leave whatever OpenStreetMap had"**, not
"blank it out" - so you can add a `description` to an already-named
building without having to retype its name. Keys starting with `_` are
ignored, which is what the `_example` entry uses.

The merge happens in the backend's `/api/buildings` endpoint, which
re-reads both files per request - so **edits take effect on a page reload**,
with no server restart and no re-running the Overpass fetch. A malformed
`building_info.json` is logged and skipped rather than breaking the map.

## Flight trails

`fts_listener.py` builds a per-registration position trail from the CoT it
receives, and serves it at
`GET /api/aircraft/{reg}/track`. While an aircraft is on the ground only its
latest fix is kept; the moment it lifts off that fix is retained as the
trail's origin, so the line starts at the runway rather than at the first
airborne fix. Touch-and-go circuits (common for these training aircraft)
briefly register as on-ground, so each circuit starts a fresh trail.

Trails are persisted to `webmap/track_history.json` (gitignored, atomic
write) and restored on startup, so restarting the backend no longer wipes a
flight's history mid-flight. This matters because **there is no free
historical-trace API to rebuild it from** - adsb.fi's `globe_history` traces
are behind a Cloudflare challenge and adsb.lol doesn't serve traces - so any
point not captured live is gone for good. A flight that was already airborne
before the listener ever saw it can't be back-filled to its real takeoff.

## Departure and arrival

These aren't filed flight plans - no free source has those. They're
*derived* from the same ground/airborne transitions that anchor the flight
trail: the position at liftoff is matched to the nearest aerodrome to give
departure, and the position at touchdown gives arrival. 146 aerodromes
within 100nm of KRAL are cached in `data/airports.geojson`.

A touchdown more than 4km from any known field is left unmatched rather
than attributed to whatever happens to be nearest.

Touch-and-go circuits are the common case for these training aircraft, so
a landing followed by a departure within 90 seconds is reclassified as a
touch-and-go: the leg continues with its original departure and a counter
is shown, instead of inventing an arrival and a new flight. A genuine
turnaround (land at Chino, sit, depart again) does start a new leg with
Chino as its departure.

A landing is recorded immediately rather than waiting to confirm the
aircraft stays put, because a parked aircraft usually stops transmitting -
waiting would mean never recording the arrival at all.

Departure is blank for any flight already airborne when the listener first
saw it; it can't be back-filled. State survives restarts via
`flight_state.json` (gitignored).

## Known limitations

- Pilot identity is not in any public ADS-B source and is shown as
  "no public source" rather than guessed.
- Building names/types/descriptions beyond what OpenStreetMap tags provide
  come from `data/building_info.json`; 147 of the 223 footprints have no
  OSM `name` and start out black until filled in.
- The campus outline is OSM relation 13218766 (`amenity=university`), a
  6-parcel multipolygon - not a hand-drawn boundary, so it reflects
  whatever OSM contributors have mapped.
- Port 8090 must be UPnP-forwarded (same as 8087/5000/19023) for this to be
  reachable outside the home LAN - see PROJECT_STATUS.md for the pattern.
