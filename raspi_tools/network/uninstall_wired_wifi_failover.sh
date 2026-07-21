#!/usr/bin/env bash
set -euo pipefail

DISPATCHER="/etc/NetworkManager/dispatcher.d/90-pipe-robot-wired-wifi-failover"
WIFI_CONNECTION="${WIFI_CONNECTION:-aokilab2}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run with sudo:"
  echo "  sudo $0"
  exit 1
fi

rm -f "$DISPATCHER"
nmcli radio wifi on >/dev/null 2>&1 || true
nmcli connection up "$WIFI_CONNECTION" >/dev/null 2>&1 || true

echo "[network] Removed wired/Wi-Fi failover dispatcher."
echo "[network] Wi-Fi was re-enabled."

