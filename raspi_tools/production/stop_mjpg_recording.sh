#!/usr/bin/env bash
set -euo pipefail

PID_DIR="${PID_DIR:-/home/haikan/pipe_robot_dev/production/run}"
mkdir -p "$PID_DIR"

for pidfile in "$PID_DIR"/record_*.pid; do
  [[ -e "$pidfile" ]] || continue
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "[record-stop] stopping pid=$pid ($pidfile)"
    kill "$pid" 2>/dev/null || true
  fi
  rm -f "$pidfile"
done

pkill -u "$(id -u)" -f "ffmpeg.*action=stream" 2>/dev/null || true
echo "[record-stop] done"
