#!/usr/bin/env bash
set -euo pipefail

ETH_CONNECTION="${ETH_CONNECTION:-netplan-eth0}"
WIFI_CONNECTION="${WIFI_CONNECTION:-aokilab2}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run with sudo:"
  echo "  sudo $0"
  exit 1
fi

echo "[network] Switching back to Wi-Fi network."
nmcli connection down "$ETH_CONNECTION" 2>/dev/null || true
nmcli radio wifi on 2>/dev/null || true
sleep 2
nmcli connection up "$WIFI_CONNECTION"

echo "[network] Current IPv4 addresses:"
ip -4 addr show wlan0
ip route

