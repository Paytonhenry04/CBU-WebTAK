#!/usr/bin/env bash
# Installs the CBU WebTAK stack as systemd units grouped under one target,
# so the whole thing is one command on/off and starts at boot.
#
#   sudo ./deploy/install.sh
#
# Afterwards:
#   sudo systemctl start cbu-tak.target     # everything on
#   sudo systemctl stop  cbu-tak.target     # everything off
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Stopping any manually-started (nohup) instances"
# Harmless if nothing matches; otherwise systemd and the old processes would
# both hold port 8090 / a TAK connection.
pkill -f "uvicorn app:app" 2>/dev/null || true
pkill -f "adsb_tak.py" 2>/dev/null || true
sleep 1

# These were previously enabled under multi-user.target. They now belong to
# cbu-tak.target, so drop the old links before re-enabling.
echo "==> Clearing any previous enablement"
systemctl disable cbu-adsb-tak.service cbu-webmap.service 2>/dev/null || true

echo "==> Installing unit files"
install -m 644 "$HERE/cbu-ftserver.service" /etc/systemd/system/
install -m 644 "$HERE/cbu-adsb-tak.service" /etc/systemd/system/
install -m 644 "$HERE/cbu-webmap.service"   /etc/systemd/system/
install -m 644 "$HERE/cbu-tak.target"       /etc/systemd/system/

echo "==> Reloading systemd"
systemctl daemon-reload

echo "==> Enabling (start at boot)"
systemctl enable cbu-ftserver.service cbu-adsb-tak.service cbu-webmap.service cbu-tak.target

echo "==> Starting everything"
systemctl start cbu-tak.target

sleep 8
echo
echo "==> Status"
systemctl --no-pager --lines=0 status cbu-tak.target || true
for u in cbu-ftserver cbu-adsb-tak cbu-webmap; do
  printf '%-16s %s\n' "$u" "$(systemctl is-active "$u.service")"
done

echo
echo "==> TAK link check"
curl -s http://127.0.0.1:8090/api/health || echo "(not answering yet - give it a few seconds)"
echo
echo
cat <<'EOT'
Done.

  sudo systemctl start cbu-tak.target    # everything on
  sudo systemctl stop  cbu-tak.target    # everything off
  systemctl status cbu-tak.target        # overview

  journalctl -u cbu-adsb-tak -f          # follow the CoT feeder
  journalctl -u cbu-webmap -f            # follow the web map
  sudo systemctl restart cbu-adsb-tak    # after editing CBU_TAILS
EOT
