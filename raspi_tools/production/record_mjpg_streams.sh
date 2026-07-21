#!/usr/bin/env bash
set -euo pipefail

LOG_ROOT="${LOG_ROOT:-/home/haikan/pipe_robot_logs/production}"
PID_DIR="${PID_DIR:-/home/haikan/pipe_robot_dev/production/run}"
HOST="${HOST:-127.0.0.1}"
SEGMENT_SEC="${SEGMENT_SEC:-600}"
WAIT_SEC="${WAIT_SEC:-45}"

mkdir -p "$LOG_ROOT/video" "$LOG_ROOT/ffmpeg" "$PID_DIR"

CAM_NAMES=("global_left" "usb_16mp" "global_right")
CAM_PORTS=("8080" "8081" "8082")

wait_for_stream() {
  local port="$1"
  local deadline=$((SECONDS + WAIT_SEC))
  while (( SECONDS <= deadline )); do
    if curl -fsS --max-time 2 "http://${HOST}:${port}/?action=snapshot" >/dev/null; then
      return 0
    fi
    sleep 2
  done
  return 1
}

echo "[record] starting camera recording"
for i in "${!CAM_NAMES[@]}"; do
  name="${CAM_NAMES[$i]}"
  port="${CAM_PORTS[$i]}"
  url="http://${HOST}:${port}/?action=stream"
  log="${LOG_ROOT}/ffmpeg/${name}_${port}.log"
  pidfile="${PID_DIR}/record_${name}_${port}.pid"
  pattern="${LOG_ROOT}/video/${name}_%Y%m%d_%H%M%S.mkv"

  if [[ -f "$pidfile" ]]; then
    old_pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "[record] $name already recording pid=$old_pid"
      continue
    fi
  fi

  if ! wait_for_stream "$port"; then
    echo "[record] skip $name: stream on port $port did not become ready" | tee -a "$log"
    continue
  fi

  echo "[record] start $name from $url"
  nohup ffmpeg -hide_banner -loglevel warning \
    -use_wallclock_as_timestamps 1 \
    -f mjpeg -i "$url" \
    -an -c:v copy \
    -f segment -strftime 1 -segment_time "$SEGMENT_SEC" -reset_timestamps 1 \
    "$pattern" >"$log" 2>&1 &
  echo "$!" >"$pidfile"
  sleep 1
done

echo "[record] active ffmpeg:"
pgrep -af "ffmpeg.*action=stream" || true
