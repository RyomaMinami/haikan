#!/usr/bin/env bash
set -euo pipefail

ETH_CONNECTION="${ETH_CONNECTION:-netplan-eth0}"
WIFI_CONNECTION="${WIFI_CONNECTION:-aokilab2}"
ETH_IFACE="${ETH_IFACE:-eth0}"
STATIC_IP="${STATIC_IP:-192.168.50.154/24}"
GATEWAY="${GATEWAY:-192.168.50.1}"
DNS="${DNS:-192.168.50.1}"
DISPATCHER="/etc/NetworkManager/dispatcher.d/90-pipe-robot-wired-wifi-failover"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Please run with sudo:"
  echo "  sudo $0"
  exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
  echo "nmcli was not found. This script expects NetworkManager."
  exit 1
fi

if ! nmcli -t -f NAME connection show | grep -Fxq "$ETH_CONNECTION"; then
  echo "[network] Connection '$ETH_CONNECTION' was not found. Creating it for ${ETH_IFACE}."
  nmcli connection add type ethernet ifname "$ETH_IFACE" con-name "$ETH_CONNECTION"
fi

if ! nmcli -t -f NAME connection show | grep -Fxq "$WIFI_CONNECTION"; then
  echo "[network] Wi-Fi connection '$WIFI_CONNECTION' was not found."
  echo "Available connections:"
  nmcli -t -f NAME,TYPE,DEVICE connection show
  exit 1
fi

BACKUP_DIR="/home/haikan/pipe_robot_dev/network/backups"
mkdir -p "$BACKUP_DIR"
nmcli connection export "$ETH_CONNECTION" "${BACKUP_DIR}/${ETH_CONNECTION}_$(date +%Y%m%d_%H%M%S).nmconnection" >/dev/null || true
nmcli connection export "$WIFI_CONNECTION" "${BACKUP_DIR}/${WIFI_CONNECTION}_$(date +%Y%m%d_%H%M%S).nmconnection" >/dev/null || true
chown -R haikan:haikan "$BACKUP_DIR" || true

echo "[network] Configure eth0 and Wi-Fi to use the same static IP, but never simultaneously."

nmcli connection modify "$ETH_CONNECTION" \
  connection.interface-name "$ETH_IFACE" \
  connection.autoconnect yes \
  connection.autoconnect-priority 100 \
  ipv4.method manual \
  ipv4.addresses "$STATIC_IP" \
  ipv4.gateway "$GATEWAY" \
  ipv4.dns "$DNS" \
  ipv4.route-metric 100

nmcli connection modify "$WIFI_CONNECTION" \
  connection.autoconnect yes \
  connection.autoconnect-priority 10 \
  ipv4.method manual \
  ipv4.addresses "$STATIC_IP" \
  ipv4.gateway "$GATEWAY" \
  ipv4.dns "$DNS" \
  ipv4.route-metric 600

cat >"$DISPATCHER" <<EOF
#!/usr/bin/env bash
set -euo pipefail

IFACE="\${1:-}"
ACTION="\${2:-}"
ETH_IFACE="$ETH_IFACE"
ETH_CONNECTION="$ETH_CONNECTION"
WIFI_CONNECTION="$WIFI_CONNECTION"
LOG_FILE="/var/log/pipe_robot_network_failover.log"

log() {
  printf '[%s] iface=%s action=%s %s\n' "\$(date '+%Y-%m-%d %H:%M:%S')" "\$IFACE" "\$ACTION" "\$*" >>"\$LOG_FILE"
}

eth_has_carrier() {
  [[ -r "/sys/class/net/\$ETH_IFACE/carrier" ]] && [[ "\$(cat "/sys/class/net/\$ETH_IFACE/carrier" 2>/dev/null)" == "1" ]]
}

case "\$IFACE:\$ACTION" in
  "\$ETH_IFACE:up"|"\$ETH_IFACE:dhcp4-change"|"\\$ETH_IFACE:connectivity-change")
    if eth_has_carrier; then
      log "wired link is active; disconnect Wi-Fi to avoid duplicate static IP"
      nmcli connection down "\$WIFI_CONNECTION" >/dev/null 2>&1 || true
    fi
    ;;
  "\$ETH_IFACE:down")
    log "wired link is down; bring Wi-Fi back"
    nmcli radio wifi on >/dev/null 2>&1 || true
    nmcli connection up "\$WIFI_CONNECTION" >/dev/null 2>&1 || true
    ;;
  *)
    if [[ "\$ACTION" == "connectivity-change" || "\$ACTION" == "hostname" ]]; then
      if eth_has_carrier && nmcli -t -f DEVICE,STATE device status | grep -q "^\\$ETH_IFACE:connected"; then
        nmcli connection down "\$WIFI_CONNECTION" >/dev/null 2>&1 || true
      fi
    fi
    ;;
esac
EOF

chmod 755 "$DISPATCHER"

echo "[network] Installed dispatcher: $DISPATCHER"
echo "[network] Apply now:"
echo "  sudo nmcli connection up '$ETH_CONNECTION'      # if LAN cable is connected"
echo "  sudo nmcli connection down '$ETH_CONNECTION'    # to test Wi-Fi fallback"
echo
echo "[network] Logs:"
echo "  sudo tail -f /var/log/pipe_robot_network_failover.log"
echo
echo "[network] SSH address remains:"
echo "  ssh haikan@${STATIC_IP%/*}"

