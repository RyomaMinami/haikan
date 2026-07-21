#!/usr/bin/env bash
set -euo pipefail

DASHBOARD_DIR="${DASHBOARD_DIR:-/home/haikan/pipe_robot_dev/camera_stream}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8090}"
LOG_DIR="${LOG_DIR:-/home/haikan/pipe_robot_logs/camera_stream}"
PID_FILE="${DASHBOARD_DIR}/camera_dashboard_${DASHBOARD_PORT}.pid"

mkdir -p "$LOG_DIR"

"$DASHBOARD_DIR/start_mjpg_3cams.sh"
"$DASHBOARD_DIR/start_camera_watchdog.sh"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 0.5
  fi
fi

pkill -u "$(id -u)" -f "dashboard_server.py.*--port ${DASHBOARD_PORT}" 2>/dev/null || true
sleep 0.5

cd "$DASHBOARD_DIR"
nohup python3 "$DASHBOARD_DIR/dashboard_server.py" --host 0.0.0.0 --port "$DASHBOARD_PORT" \
  >"${LOG_DIR}/camera_dashboard_${DASHBOARD_PORT}.log" 2>&1 &
echo "$!" >"$PID_FILE"

echo "Dashboard: http://$(hostname -I | awk '{print $1}'):${DASHBOARD_PORT}/robot_dashboard.html"
