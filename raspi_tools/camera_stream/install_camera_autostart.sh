#!/usr/bin/env bash
set -euo pipefail

DASHBOARD_DIR="${DASHBOARD_DIR:-/home/haikan/pipe_robot_dev/camera_stream}"
LOG_DIR="${LOG_DIR:-/home/haikan/pipe_robot_logs/camera_stream}"
START_SCRIPT="${DASHBOARD_DIR}/start_camera_dashboard.sh"
MARKER="pipe-robot-camera-dashboard"

mkdir -p "$LOG_DIR"

if [[ ! -x "$START_SCRIPT" ]]; then
  chmod +x "$START_SCRIPT"
fi

tmp="$(mktemp)"
crontab -l 2>/dev/null | grep -v "$MARKER" >"$tmp" || true
{
  cat "$tmp"
  echo "@reboot sleep 20; ${START_SCRIPT} >> ${LOG_DIR}/autostart.log 2>&1 # ${MARKER}"
} | crontab -
rm -f "$tmp"

echo "[autostart] installed cron @reboot entry for camera/dashboard"
crontab -l | grep "$MARKER" || true
