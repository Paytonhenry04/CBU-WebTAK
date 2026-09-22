# HowTo

## The two web pages

1. **FTS Admin Dashboard** - server health/status only, no map.
   `https://fts.paytons-mc-server.duckdns.org/`
   Login: `admin` / (see `ftserver/.env`, or ask Payton - not written here on purpose)

2. **CBU/KRAL Live Web Map** - the actual live aircraft map.
   `https://paytons-mc-server.duckdns.org/`
   No login, public.

(Both are HTTPS via a Caddy reverse proxy - see `deploy/HTTPS.md` for setup
and `deploy/Caddyfile` for the config. The old `http://...:5000` and
`http://...:8090` URLs stop working once the plain-HTTP ports are closed,
step 7 of that setup.)

## Starting everything (recommended: run as services)

One-time setup. After this, everything starts on boot and restarts itself
if it crashes:

```bash
cd ~/TAK
sudo ./deploy/install.sh
```

### One command on/off

```bash
sudo systemctl start cbu-tak.target    # FTS + CoT feeder + web map, all on
sudo systemctl stop  cbu-tak.target    # all off
systemctl status cbu-tak.target        # overview
```

`cbu-tak.target` groups the three services. Stopping it stops all of them
(each declares `PartOf=`, which is what makes stop propagate - `Wants=`
alone only works on start).

### Day to day

```bash
journalctl -u cbu-adsb-tak -f          # watch the CoT feeder
journalctl -u cbu-webmap -f            # watch the web map
sudo systemctl restart cbu-adsb-tak    # after editing CBU_TAILS
sudo systemctl restart cbu-webmap      # after editing backend code
```

Stopping the target runs `docker-compose stop`, not `down` - the
containers are kept rather than recreated, which avoids the
docker-compose v1 `KeyError: 'ContainerConfig'` bug this project has hit
repeatedly. The FTS database and credentials live in named volumes and
are not affected either way.

Why this matters: `adsb_tak.py` has no internal reconnect and exits when
FreeTAKServer restarts. Since the web map's only data source is CoT from
FTS, that used to take the map's data down with it. systemd restarting it
automatically is what covers that.

## Starting everything manually (if you're not using the services)

Run these from `~/TAK`, in order.

### 1. Start FreeTAKServer (needed for the dashboard, and for real TAK clients like ATAK)

```bash
cd ~/TAK/ftserver
docker-compose -f compose.yaml up -d
```

### 2. Start the ADS-B -> FTS feeder (needed for ATAK/real TAK clients and the dashboard's live data - NOT needed for the web map itself)

```bash
cd ~/TAK
source venv/bin/activate
nohup python adsb_tak.py > adsb_tak.log 2>&1 &
disown
```

### 3. Start the web map backend (this is what powers page 2 - it polls adsb.fi directly, independent of steps 1-2)

```bash
cd ~/TAK/webmap/backend
nohup ../../venv/bin/uvicorn app:app --host 0.0.0.0 --port 8090 > ../webmap.log 2>&1 &
disown
```

## Adding building names / descriptions

`webmap/data/building_info.json` already has an entry for every building
(223 of them), pre-filled with whatever OpenStreetMap knew. To edit one:

1. Click a building on the map - the popup shows its `osm_id`.
2. Find that id in the file and fill in what you want:

```json
"233663002": {
  "name": "Engineering Building",
  "type": "Academic",
  "description": "Whatever you want shown in the popup."
}
```

3. Reload the page. That's it - no restart, no re-fetching OSM data.

Notes:
- Works on both grey (unnamed) and yellow (named) buildings.
- Leaving a field blank keeps whatever OSM had - so you can add a
  description to a named building without retyping its name.
- Named buildings are listed first in the file, then the 147 blank ones.
- A JSON typo won't break the map, the file just gets ignored (check
  `webmap.log` if an entry doesn't show up).

## Checking things are running

```bash
docker ps                        # freetakserver + freetakserver-ui should both say "Up"
ps aux | grep -E "adsb_tak|uvicorn"
curl http://127.0.0.1:8090/api/health
```

## If something looks broken

- **Dashboard all red / "connection issue"**: `docker ps` - if either FTS container isn't "Up", `cd ~/TAK/ftserver && docker-compose -f compose.yaml up -d` again.
- **Web map shows no planes**: check `curl http://127.0.0.1:8090/api/health` - if `tracked_aircraft` is `0`, that just means no CBU tail is airborne right now, not a bug.
- **`adsb_tak.py` not in the process list**: it has no auto-reconnect yet, so it exits if FTS restarts. Just re-run step 2.
