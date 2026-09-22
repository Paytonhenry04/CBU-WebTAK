# HTTPS setup (one-time)

This is a step-by-step for the first setup only. Once done, `install.sh`
handles Caddy on every future run automatically. Full design reasoning is
in `PROJECT_STATUS.md`'s Caddy section - this file is just the checklist.

Three sites, three subdomains, all off the same DuckDNS name:

| Site | URL | Proxies to |
|---|---|---|
| Web map (public, no login) | `https://paytons-mc-server.duckdns.org` | `127.0.0.1:8090` |
| FTS dashboard | `https://fts.paytons-mc-server.duckdns.org` | `127.0.0.1:5000` |
| FTS API (Socket.IO) | `https://api.paytons-mc-server.duckdns.org` | `127.0.0.1:19023` |

## 0. Before starting

```bash
sudo ss -tlnp '( sport = :80 or sport = :443 )'   # must print nothing
```

## 1. Install Caddy

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install caddy
```

Use the packaged `caddy.service` - don't write a custom unit. It already
runs as an unprivileged `caddy` user with `CAP_NET_BIND_SERVICE` (so no root
needed for 80/443) and stores certificates under
`/var/lib/caddy/.local/share/caddy`, which must survive reboots.

## 2. Forward 80 and 443

```
! upnpc -a 192.168.254.140 80 80 TCP
! upnpc -a 192.168.254.140 443 443 TCP
! upnpc -l | grep -E ' (80|443)->'
```

Port 80 isn't strictly required for issuance (Caddy can use TLS-ALPN-01 on
443 alone), but forward it anyway so `http://` visitors get redirected
instead of hanging, and as an HTTP-01 fallback.

**Two things worth checking here, not assuming:**
- UPnP may refuse ports below 1024 on some routers. If the `upnpc -a` calls
  fail, add the forwards manually in the router admin UI instead
  (`http://192.168.254.254`, while on the LAN).
- The Frontier router's own admin UI listens on port 5000 *internally* -
  confirm it isn't also squatting on the *external* 80/443 for remote
  management, which would shadow Caddy. `curl -I http://<public-ip>/` from
  outside should hit Caddy's default page, not a router login screen.

## 3. Run install.sh once with staging certificates

`deploy/Caddyfile` ships with `acme_ca https://acme-staging-v02...`
**enabled by default** - leave it that way for this step. Let's Encrypt
allows only 5 failed validations per hostname per hour; staging has no
meaningful limit, so all the port-forwarding trial-and-error is free there.

```bash
! cd ~/TAK && sudo ./deploy/install.sh
sudo journalctl -u caddy -n 40
```

Expect "certificate obtained successfully" for all three hostnames. From a
genuinely different network (not the LAN - that would hairpin and prove
nothing):

```bash
curl -kI https://paytons-mc-server.duckdns.org/
```

`-k` is required here - staging certs are untrusted by design, so this
should show a 200 with a cert warning suppressed, not a real failure.

## 4. Switch to production certificates

Edit `deploy/Caddyfile`, comment out the `acme_ca` line, then:

```bash
! sudo caddy validate --config /home/payton/TAK/deploy/Caddyfile
! sudo cp /home/payton/TAK/deploy/Caddyfile /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

From off-network, no `-k` should be needed anywhere from here on:

```bash
for h in paytons-mc-server.duckdns.org fts.paytons-mc-server.duckdns.org api.paytons-mc-server.duckdns.org; do
  echo | openssl s_client -connect $h:443 -servername $h 2>/dev/null | openssl x509 -noout -subject -issuer -dates
done
```

Expect issuer `Let's Encrypt`, subject matching each hostname, ~90 days.

## 5. The web map (verify this part first - it can't break anything)

Browser, off-network: `https://paytons-mc-server.duckdns.org`. Check:
padlock with no warning, zero `Mixed Content` lines in DevTools Console,
`/api/aircraft` returning 200 on its poll interval.

## 6. The FTS dashboard (the part that can break)

The `ftserver/compose.yaml` change (already made) points the dashboard's
Socket.IO connection at `https://api.paytons-mc-server.duckdns.org:443`
instead of `http://...duckdns.org:19023`. This requires a restart, which
recreates both FTS containers:

```bash
! sudo systemctl restart cbu-ftserver
```

Confirm two things that took real debugging earlier and must survive the
recreate (both live on the named `free-tak-ui-db`/core volumes, so `stop`+
`rm -f`, which `ftserver-up.sh` uses, doesn't touch them - **never run
`docker-compose down -v`**, that destroys the FTS database and these fixes):

```bash
docker exec ftserver_freetakserver-ui_1 head -c 60 \
  /home/freetak/.local/lib/python3.11/site-packages/FreeTAKServer-UI/app/base/static/assets/js/socket.io-1.4.5.js
# must show "Socket.IO v4.7.5", not the old v1.4.5

docker exec ftserver_freetakserver_1 grep -c allow_upgrades \
  /home/freetak/FreeTAKServer/services/rest_api_service/rest_api_service_main.py
# must print 1
```

Then confirm the container's own server-side call reaches Caddy through
the `extra_hosts` override:

```bash
docker exec ftserver_freetakserver-ui_1 python3 -c \
  "import requests; print(requests.get('https://api.paytons-mc-server.duckdns.org', timeout=10).status_code)"
```

A TLS or connection error here means the `extra_hosts` → `172.28.0.1`
route isn't reaching Caddy - see "If it breaks" below before touching the
public browser test.

**Now the actual check**, off-network: log into
`https://fts.paytons-mc-server.duckdns.org`. View Source and confirm the
`io.connect(...)` line reads `https://api.paytons-mc-server.duckdns.org:443`
- that's the whole point of this change. Then confirm **System Status,
Uptime, Connected Clients, Health, and Logs all populate** - this is the
exact set of gauges that took significant debugging to get working
earlier, so treat anything blank or stuck loading as a stop-and-investigate,
not a minor glitch. `/user` (profile page) has a known, separate,
pre-existing bug unrelated to this change - its socket is hardcoded to
`http://` in the FTS-UI image and will show mixed-content errors; that's
not something this change touches.

## 7. Close the old plain-HTTP ports

Only after steps 5 and 6 both fully pass:

```
! upnpc -d 5000 TCP
! upnpc -d 19023 TCP
! upnpc -d 8090 TCP
! upnpc -l
```

**Do not touch 8087 or 2223** - CoT/TAK traffic and SSH, neither is HTTP,
neither goes through Caddy.

Re-run the step 5 and step 6 checks from off-network one more time after
closing the ports, to catch anything that was quietly still using a direct
port.

## If it breaks

```bash
cd ~/TAK && git diff ftserver/compose.yaml     # see exactly what changed
git checkout ftserver/compose.yaml             # revert
sudo systemctl restart cbu-ftserver
sudo systemctl stop caddy                      # if the proxy itself is the problem
```

The old plain-HTTP ports stay forwarded through step 6 specifically so this
rollback path exists - don't close them until both sites are confirmed
working over HTTPS.
