#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORTS=(8080 8081 8082)

ps -u "$(id -u)" -o pid,cmd | grep -E "mjpg_streamer.*input_uvc.so" | grep -v grep || true
echo

for port in "${PORTS[@]}"; do
  url="http://${HOST}:${port}/?action=snapshot"
  printf "[mjpg] %-55s " "$url"
  if timeout 6 curl -fsS --connect-timeout 1 --max-time 4 "$url" >/tmp/mjpg_snapshot_${port}.jpg; then
    bytes="$(wc -c </tmp/mjpg_snapshot_${port}.jpg)"
    echo "OK ${bytes} bytes"
  else
    echo "NG"
  fi
done
