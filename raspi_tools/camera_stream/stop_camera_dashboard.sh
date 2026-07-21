#!/usr/bin/env bash
set -euo pipefail

DASHBOARD_DIR="${DASHBOARD_DIR:-/home/haikan/pipe_robot_dev/camera_stream}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8090}"
PID_FILE="${DASHBOARD_DIR}/camera_dashboard_${DASHBOARD_PORT}.pid"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
fi

"$DASHBOARD_DIR/stop_camera_watchdog.sh" || true

echo "Dashboard stopped"
