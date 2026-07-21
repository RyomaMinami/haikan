#!/usr/bin/env bash
set -euo pipefail

CONNECTION="${CONNECTION:-aokilab2}"
STATIC_IP="${STATIC_IP:-192.168.50.154/24}"
GATEWAY="${GATEWAY:-192.168.50.1}"
DNS="${DNS:-192.168.50.1}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run with sudo:"
  echo "  sudo $0"
  exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli was not found. This script expects NetworkManager."
  exit 1
fi

if ! nmcli -t -f NAME connection show | grep -Fxq "$CONNECTION"; then
  echo "NetworkManager connection '$CONNECTION' was not found."
  echo "Available connections:"
  nmcli -t -f NAME,TYPE,DEVICE connection show
  exit 1
fi

BACKUP_DIR="/home/haikan/pipe_robot_dev/network/backups"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="${BACKUP_DIR}/${CONNECTION}_$(date +%Y%m%d_%H%M%S).nmconnection"

nmcli connection export "$CONNECTION" "$BACKUP_FILE" >/dev/null
chown -R haikan:haikan "$BACKUP_DIR" || true

echo "[network] Backup saved: $BACKUP_FILE"
echo "[network] Setting $CONNECTION to static IPv4:"
echo "          address: $STATIC_IP"
echo "          gateway: $GATEWAY"
echo "          dns    : $DNS"

nmcli connection modify "$CONNECTION" \
  ipv4.method manual \
  ipv4.addresses "$STATIC_IP" \
  ipv4.gateway "$GATEWAY" \
  ipv4.dns "$DNS" \
  connection.autoconnect yes

echo "[network] Done. Reconnect Wi-Fi or reboot to apply:"
echo "  sudo nmcli connection down '$CONNECTION'; sudo nmcli connection up '$CONNECTION'"
echo
echo "[network] If SSH disconnects, wait about 30 seconds and reconnect to:"
echo "  ssh haikan@${STATIC_IP%/*}"

