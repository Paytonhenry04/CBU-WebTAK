# CBU/KRAL Live Web Map

Publicly-accessible live map of CBU's tracked aircraft. Runs alongside
`adsb_tak.py` and the `ftserver/` Docker stack.

## Data flow

```
                          adsb.fi
                         /        \
                    poll            poll
                   /                    \
          adsb_tak.py                adsb_poller.py
                |                          |
           CoT/TCP                    in-memory
                |                    AIRCRAFT_STATE
                v                          |
        FreeTAKServer (:8087)     GET /api/aircraft  (polled by browser)
        (real TAK clients,                 |
         e.g. ATAK, connect here)           v
                              frontend/ (MapLibre GL JS) renders it
```

`adsb_tak.py` and `adsb_poller.py` both poll adsb.fi independently - they
were *not* wired together (FTS's own CoT relay-to-other-clients turned out
to be unreliable, see "Why not fed from FTS" below), so the web map is
effectively a second, parallel consumer of the same public data source, not
downstream of FTS.

`adsb_tak.py` still embeds aircraft type/owner/ICAO-hex in a custom
`<aircraft>` CoT detail element sent to FTS (spec-legal, ignored by real TAK
clients) - that part of the original design is real and unaffected: any
actual TAK client (ATAK, WinTAK) connecting to FTS gets that richer contact
info. It just isn't how the *web map itself* gets its data.

## Why not fed from FTS

The original design had a second `pytak` client (`fts_listener.py`, now
removed) connect to FTS's port 8087 purely to read back the same CoT
`adsb_tak.py` sends in, so the web map really would be downstream of FTS.
This works in principle - FTS does relay CoT between independently
connected TCP clients - but testing found it unreliable in practice:

- Listener connects, then sender joins → relay worked, richer fields
  (type/owner/hex) parsed correctly.
- Sender already connected, listener joins later → nothing relayed.
- Retried with the "listener first" ordering enforced end-to-end → still
  nothing relayed on a later attempt.

No reliable ordering rule could be established, and FTS's own logs show an
internal exception on effectively every new connection
(`can only concatenate str (not "NoneType") to str`), suggesting the
relay/connection bookkeeping on this FTS version is itself flaky. Rather
than ship a map that silently shows zero aircraft some fraction of the
time depending on connection-timing luck, `adsb_poller.py` polls adsb.fi
directly instead - the same simple approach `adsb_tak.py` has used
reliably all along.

## Running it

```bash
# one-time: fetch/refresh cached OSM building + airport geometry
cd webmap/data && ../../venv/bin/python3 fetch_osm_data.py

# start the backend (serves the frontend + /api/aircraft)
cd webmap/backend
../../venv/bin/uvicorn app:app --host 0.0.0.0 --port 8090
```

No particular start order relative to `adsb_tak.py`/FTS is required -
`adsb_poller.py` polls adsb.fi independently.

## Refreshing the OSM cache

`webmap/data/cbu_buildings.geojson` and `kral_airport.geojson` are static,
fetched once via the Overpass API and committed as plain files - nothing at
runtime hits Overpass. Re-run `fetch_osm_data.py` manually if the campus
adds buildings or the cached data otherwise goes stale. The Overpass public
instance is occasionally slow/returns a transient 504 - just retry.

## Known limitations

- Aircraft "departure/arrival airport" and "pilot" are not available from
  any public ADS-B source, and are deliberately shown as
  "not available (no public source)" in the UI rather than guessed.
- Building info popups only have what OpenStreetMap tags provide (name +
  building/amenity type) - most of the 223 cached CBU building footprints
  have no `name` tag and show as "Unnamed building".
- Port 8090 must be UPnP-forwarded (same as 8087/5000/19023) for this to be
  reachable outside the home LAN - see PROJECT_STATUS.md for the pattern.
