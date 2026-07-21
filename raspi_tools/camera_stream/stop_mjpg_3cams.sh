#!/usr/bin/env bash
set -euo pipefail

STREAM_ROOT="${STREAM_ROOT:-/home/haikan/pipe_robot_dev/camera_stream}"
LOCK_FILE="${STREAM_ROOT}/mjpg_3cams.lock"

mkdir -p "$STREAM_ROOT"
exec 9>"$LOCK_FILE"
flock 9

if [[ -d "$STREAM_ROOT" ]]; then
  for pidfile in "$STREAM_ROOT"/global_left_8080.pid \
                 "$STREAM_ROOT"/usb_16mp_8081.pid \
                 "$STREAM_ROOT"/global_right_8082.pid; do
    [[ -e "$pidfile" ]] || continue
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "[mjpg] stop pid=$pid"
      kill "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  done
fi

pkill -u "$(id -u)" -f "mjpg_streamer.*input_uvc.so" 2>/dev/null || true
echo "[mjpg] stopped"
