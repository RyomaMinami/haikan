#!/usr/bin/env bash
set -euo pipefail

CONNECTION="${CONNECTION:-aokilab2}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run with sudo:"
  echo "  sudo $0"
  exit 1
fi

echo "[network] Restoring $CONNECTION to DHCP/auto IPv4"
nmcli connection modify "$CONNECTION" \
  ipv4.method auto \
  ipv4.addresses "" \
  ipv4.gateway "" \
  ipv4.dns ""

echo "[network] Done. Reconnect Wi-Fi or reboot to apply:"
echo "  sudo nmcli connection down '$CONNECTION'; sudo nmcli connection up '$CONNECTION'"

