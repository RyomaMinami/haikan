#!/usr/bin/env bash
set -euo pipefail

STREAM_ROOT="${STREAM_ROOT:-/home/haikan/pipe_robot_dev/camera_stream}"
LOG_DIR="${LOG_DIR:-/home/haikan/pipe_robot_logs/camera_stream}"
INTERVAL_SEC="${INTERVAL_SEC:-10}"
FAIL_LIMIT="${FAIL_LIMIT:-2}"
COOLDOWN_SEC="${COOLDOWN_SEC:-30}"
DEVICE_GRACE_SEC="${DEVICE_GRACE_SEC:-5}"

mkdir -p "$LOG_DIR"

fail_count=0
last_restart=0

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

check_one() {
  local port="$1"
  timeout 6 curl -fsS --connect-timeout 1 --max-time 4 "http://127.0.0.1:${port}/?action=snapshot" -o /dev/null
}

camera_devices_present() {
  [[ -e /dev/v4l/by-path/platform-xhci-hcd.0-usb-0:1:1.0-video-index0 || \
     -e /dev/v4l/by-path/platform-xhci-hcd.0-usbv2-0:1:1.0-video-index0 ]] && \
  [[ -e /dev/v4l/by-path/platform-xhci-hcd.1-usb-0:1:1.0-video-index0 || \
     -e /dev/v4l/by-path/platform-xhci-hcd.1-usbv2-0:1:1.0-video-index0 || \
     -e /dev/v4l/by-id/usb-16MP_Camera_Mamufacture_16MP_USB_Camera_2022050701-video-index0 ]] && \
  [[ -e /dev/v4l/by-path/platform-xhci-hcd.0-usb-0:2:1.0-video-index0 || \
     -e /dev/v4l/by-path/platform-xhci-hcd.0-usbv2-0:2:1.0-video-index0 ]]
}

restart_streams() {
  log "restarting mjpg streams"
  "$STREAM_ROOT/stop_mjpg_3cams.sh" >>"$LOG_DIR/camera_watchdog_restart.log" 2>&1 || true
  sleep "$DEVICE_GRACE_SEC"
  "$STREAM_ROOT/start_mjpg_3cams.sh" >>"$LOG_DIR/camera_watchdog_restart.log" 2>&1 || true
}

log "camera watchdog started"

while true; do
  ok=1
  for port in 8080 8081 8082; do
    if ! check_one "$port" >/dev/null 2>&1; then
      ok=0
      log "camera port ${port} snapshot failed"
    fi
  done

  if [[ "$ok" -eq 1 ]]; then
    fail_count=0
  else
    fail_count=$((fail_count + 1))
    if [[ "$fail_count" -ge "$FAIL_LIMIT" ]]; then
      now="$(date +%s)"
      if (( now - last_restart >= COOLDOWN_SEC )); then
        if camera_devices_present; then
          log "restart after ${fail_count} failed checks"
        else
          log "some camera devices are missing; restart anyway and let start script wait"
        fi
        restart_streams
        last_restart="$now"
        fail_count=0
      else
        log "restart suppressed by cooldown"
      fi
    fi
  fi

  sleep "$INTERVAL_SEC"
done
