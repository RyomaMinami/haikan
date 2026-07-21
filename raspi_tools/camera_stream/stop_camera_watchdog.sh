#!/usr/bin/env bash
set -euo pipefail

STREAM_ROOT="${STREAM_ROOT:-/home/haikan/pipe_robot_dev/camera_stream}"
PID_FILE="${STREAM_ROOT}/camera_watchdog.pid"

if [[ -f "$PID_FILE" ]]; then
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    echo "[watchdog] stopped pid=$pid"
  fi
  rm -f "$PID_FILE"
fi
