# HowTo

## The two web pages

1. **FTS Admin Dashboard** - server health/status only, no map.
   `http://paytons-mc-server.duckdns.org:5000/`
   Login: `admin` / (see `ftserver/.env`, or ask Payton - not written here on purpose)

2. **CBU/KRAL Live Web Map** - the actual live aircraft map.
   `http://paytons-mc-server.duckdns.org:8090/`
   No login, public.

## Starting everything

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
