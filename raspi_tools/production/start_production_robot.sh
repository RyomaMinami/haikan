#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE:-/home/haikan/pipe_robot_dev}"
PROD="${PROD:-${BASE}/production}"
LOG_ROOT="${LOG_ROOT:-/home/haikan/pipe_robot_logs/production}"
RUN_DIR="${RUN_DIR:-${PROD}/run}"
DASHBOARD_PORT="${DASHBOARD_PORT:-8090}"

mkdir -p "$LOG_ROOT" "$RUN_DIR"

echo "[production] starting camera streams in background"
pkill -u "$(id -u)" -f "start_mjpg_3cams.sh" 2>/dev/null || true
nohup env START_DELAY_SEC="${START_DELAY_SEC:-20}" PER_CAMERA_DELAY_SEC="${PER_CAMERA_DELAY_SEC:-5}" \
  "${BASE}/camera_stream/start_mjpg_3cams.sh" \
  >"${LOG_ROOT}/start_mjpg_3cams.log" 2>&1 &
echo "$!" >"${RUN_DIR}/start_mjpg_3cams.pid"
"${BASE}/camera_stream/start_camera_watchdog.sh" >/dev/null 2>&1 || true

echo "[production] starting dashboard server"
dashboard_pidfile="${BASE}/camera_stream/camera_dashboard_${DASHBOARD_PORT}.pid"
if [[ -f "$dashboard_pidfile" ]]; then
  old_pid="$(cat "$dashboard_pidfile" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 0.5
  fi
fi
pkill -u "$(id -u)" -f "dashboard_server.py.*--port ${DASHBOARD_PORT}" 2>/dev/null || true
sleep 0.5
cd "${BASE}/camera_stream"
nohup python3 "${BASE}/camera_stream/dashboard_server.py" --host 0.0.0.0 --port "$DASHBOARD_PORT" \
  >"${LOG_ROOT}/camera_dashboard_${DASHBOARD_PORT}.log" 2>&1 &
echo "$!" >"$dashboard_pidfile"
cd - >/dev/null

echo "[production] starting API state logger"
state_pidfile="${RUN_DIR}/state_api_logger.pid"
if [[ -f "$state_pidfile" ]]; then
  old_pid="$(cat "$state_pidfile" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 0.5
  fi
fi
nohup python3 "${PROD}/state_api_logger.py" \
  --url "http://127.0.0.1:${DASHBOARD_PORT}/api/state" \
  --output-dir "$LOG_ROOT" \
  --interval 0.2 \
  >"${LOG_ROOT}/state_api_logger.log" 2>&1 &
echo "$!" >"$state_pidfile"

echo "[production] starting camera recording watchdog"
record_watch_pidfile="${RUN_DIR}/recording_watchdog.pid"
if [[ -f "$record_watch_pidfile" ]]; then
  old_pid="$(cat "$record_watch_pidfile" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 0.5
  fi
fi
nohup "${PROD}/recording_watchdog.sh" >"${LOG_ROOT}/recording_watchdog.log" 2>&1 &
echo "$!" >"$record_watch_pidfile"

echo
echo "[production] ready"
echo "  dashboard wired: http://192.168.0.218:${DASHBOARD_PORT}/robot_dashboard.html"
echo "  dashboard wifi : http://192.168.50.154:${DASHBOARD_PORT}/robot_dashboard.html"
echo "  logs           : ${LOG_ROOT}"
echo
pgrep -af 'dashboard_server|mjpg_streamer|state_api_logger|ffmpeg.*action=stream' || true
