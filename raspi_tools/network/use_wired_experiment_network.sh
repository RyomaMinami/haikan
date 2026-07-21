#!/usr/bin/env bash
set -euo pipefail

ETH_CONNECTION="${ETH_CONNECTION:-netplan-eth0}"
WIFI_CONNECTION="${WIFI_CONNECTION:-aokilab2}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run with sudo:"
  echo "  sudo $0"
  exit 1
fi

echo "[network] Switching to wired experiment network."
echo "[network] Wi-Fi will be disconnected to avoid using the same IP on two interfaces."

nmcli connection down "$WIFI_CONNECTION" 2>/dev/null || true
nmcli radio wifi off 2>/dev/null || true
nmcli connection up "$ETH_CONNECTION"

echo "[network] Current IPv4 addresses:"
ip -4 addr show eth0
ip route

