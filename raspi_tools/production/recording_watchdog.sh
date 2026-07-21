#!/usr/bin/env bash
set -euo pipefail

PROD="${PROD:-/home/haikan/pipe_robot_dev/production}"
INTERVAL_SEC="${INTERVAL_SEC:-30}"
LOG_ROOT="${LOG_ROOT:-/home/haikan/pipe_robot_logs/production}"

mkdir -p "$LOG_ROOT"
echo "[record-watch] started interval=${INTERVAL_SEC}s"

while true; do
  # Idempotent: record_mjpg_streams.sh starts only missing camera recorders.
  WAIT_SEC="${WAIT_SEC:-8}" "${PROD}/record_mjpg_streams.sh" || true
  sleep "$INTERVAL_SEC"
done
