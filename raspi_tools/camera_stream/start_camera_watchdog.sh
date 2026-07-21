#!/usr/bin/env bash
set -euo pipefail

STREAM_ROOT="${STREAM_ROOT:-/home/haikan/pipe_robot_dev/camera_stream}"
LOG_DIR="${LOG_DIR:-/home/haikan/pipe_robot_logs/camera_stream}"
PID_FILE="${STREAM_ROOT}/camera_watchdog.pid"

mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "[watchdog] already running pid=$old_pid"
    exit 0
  fi
fi

nohup "$STREAM_ROOT/camera_watchdog.sh" >"$LOG_DIR/camera_watchdog.log" 2>&1 &
echo "$!" >"$PID_FILE"
echo "[watchdog] started pid=$(cat "$PID_FILE")"
