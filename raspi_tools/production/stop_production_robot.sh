#!/usr/bin/env bash
set -euo pipefail

BASE="${BASE:-/home/haikan/pipe_robot_dev}"
PROD="${PROD:-${BASE}/production}"
RUN_DIR="${RUN_DIR:-${PROD}/run}"

"${PROD}/stop_mjpg_recording.sh" || true

record_watch_pidfile="${RUN_DIR}/recording_watchdog.pid"
if [[ -f "$record_watch_pidfile" ]]; then
  pid="$(cat "$record_watch_pidfile" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "[production-stop] stopping recording watchdog pid=$pid"
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$record_watch_pidfile"
fi

state_pidfile="${RUN_DIR}/state_api_logger.pid"
if [[ -f "$state_pidfile" ]]; then
  pid="$(cat "$state_pidfile" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "[production-stop] stopping state logger pid=$pid"
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$state_pidfile"
fi

"${BASE}/camera_stream/stop_camera_dashboard.sh" || true
"${BASE}/camera_stream/stop_mjpg_3cams.sh" || true
"${BASE}/camera_stream/stop_camera_watchdog.sh" || true
pkill -u "$(id -u)" -f "start_mjpg_3cams.sh" 2>/dev/null || true

echo "[production-stop] done"
