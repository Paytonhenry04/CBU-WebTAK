#!/usr/bin/env bash
# Installs the CBU WebTAK services so they start on boot and restart on
# failure. Run with sudo:  sudo ./deploy/install.sh
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

echo "==> Installing unit files"
install -m 644 "$HERE/cbu-adsb-tak.service" /etc/systemd/system/
install -m 644 "$HERE/cbu-webmap.service"   /etc/systemd/system/

echo "==> Reloading systemd"
systemctl daemon-reload

echo "==> Enabling and starting"
systemctl enable --now cbu-adsb-tak.service
systemctl enable --now cbu-webmap.service

sleep 5
echo
echo "==> Status"
systemctl --no-pager --lines=0 status cbu-adsb-tak.service || true
systemctl --no-pager --lines=0 status cbu-webmap.service || true

echo
echo "==> TAK link check"
curl -s http://127.0.0.1:8090/api/health || echo "(web map not answering yet - give it a few seconds)"
echo
echo
echo "Done. Useful commands:"
echo "  journalctl -u cbu-adsb-tak -f      # follow the CoT feeder"
echo "  journalctl -u cbu-webmap -f        # follow the web map"
echo "  systemctl restart cbu-webmap       # restart one service"
