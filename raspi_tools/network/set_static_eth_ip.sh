#!/usr/bin/env bash
set -euo pipefail

CONNECTION="${CONNECTION:-netplan-eth0}"
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
  echo "[network] Connection '$CONNECTION' was not found. Creating it for eth0."
  nmcli connection add type ethernet ifname eth0 con-name "$CONNECTION"
fi

BACKUP_DIR="/home/haikan/pipe_robot_dev/network/backups"
mkdir -p "$BACKUP_DIR"
BACKUP_FILE="${BACKUP_DIR}/${CONNECTION}_$(date +%Y%m%d_%H%M%S).nmconnection"

nmcli connection export "$CONNECTION" "$BACKUP_FILE" >/dev/null || true
chown -R haikan:haikan "$BACKUP_DIR" || true

echo "[network] Backup saved: $BACKUP_FILE"
echo "[network] Setting $CONNECTION / eth0 to static IPv4:"
echo "          address: $STATIC_IP"
echo "          gateway: $GATEWAY"
echo "          dns    : $DNS"

nmcli connection modify "$CONNECTION" \
  connection.interface-name eth0 \
  connection.autoconnect yes \
  ipv4.method manual \
  ipv4.addresses "$STATIC_IP" \
  ipv4.gateway "$GATEWAY" \
  ipv4.dns "$DNS" \
  ipv4.route-metric 100

echo "[network] Done."
echo "[network] Apply when the LAN cable is connected:"
echo "  sudo nmcli connection up '$CONNECTION'"
echo
echo "[network] Important: do not keep Wi-Fi and eth0 using the same IP at the same time."

