#!/usr/bin/env bash
set -euo pipefail

STREAM_ROOT="${STREAM_ROOT:-/home/haikan/pipe_robot_dev/camera_stream}"
LOG_DIR="${LOG_DIR:-/home/haikan/pipe_robot_logs/camera_stream}"
WWW_DIR="${WWW_DIR:-/usr/local/share/mjpg-streamer/www}"
MJPG_STREAMER="${MJPG_STREAMER:-/usr/local/bin/mjpg_streamer}"

WIDTH="${WIDTH:-320}"
HEIGHT="${HEIGHT:-240}"
FPS="${FPS:-5}"
QUALITY="${QUALITY:-60}"
CAMERA_WAIT_SEC="${CAMERA_WAIT_SEC:-30}"
START_DELAY_SEC="${START_DELAY_SEC:-20}"
PER_CAMERA_DELAY_SEC="${PER_CAMERA_DELAY_SEC:-5}"
LOCK_FILE="${STREAM_ROOT}/mjpg_3cams.lock"

mkdir -p "$STREAM_ROOT" "$LOG_DIR"

CAM_NAMES=("global_left" "usb_16mp" "global_right")
CAM_DEVICE_CANDIDATES=(
  "/dev/v4l/by-path/platform-xhci-hcd.1-usb-0:1:1.0-video-index0 /dev/v4l/by-path/platform-xhci-hcd.1-usbv2-0:1:1.0-video-index0"
  "/dev/v4l/by-path/platform-xhci-hcd.0-usb-0:2:1.0-video-index0 /dev/v4l/by-path/platform-xhci-hcd.0-usbv2-0:2:1.0-video-index0 /dev/v4l/by-id/usb-16MP_Camera_Mamufacture_16MP_USB_Camera_2022050701-video-index0"
  "/dev/v4l/by-path/platform-xhci-hcd.1-usb-0:2:1.0-video-index0 /dev/v4l/by-path/platform-xhci-hcd.1-usbv2-0:2:1.0-video-index0"
)
CAM_PORTS=("8080" "8081" "8082")
USED_REAL_DEVICES=()

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[mjpg] another start/stop operation is running"
  exit 0
fi

echo "[mjpg] boot/start delay ${START_DELAY_SEC}s to avoid USB camera inrush/enumeration race"
sleep "$START_DELAY_SEC"

detect_usb_index0_devices() {
  local seen=""
  for dev in /dev/v4l/by-path/platform-xhci*-video-index0; do
    [[ -e "$dev" ]] || continue
    real="$(readlink -f "$dev" 2>/dev/null || true)"
    [[ -n "$real" ]] || continue
    case " $seen " in
      *" $real "*) continue ;;
    esac
    seen="$seen $real"
    echo "$dev"
  done
}

is_used_device() {
  local real="$1"
  local used
  for used in "${USED_REAL_DEVICES[@]}"; do
    [[ "$used" == "$real" ]] && return 0
  done
  return 1
}

resolve_device() {
  local candidates="$1"
  local fallback_index="${2:-}"
  local deadline=$((SECONDS + CAMERA_WAIT_SEC))
  while (( SECONDS <= deadline )); do
    for dev in $candidates; do
      if [[ -e "$dev" ]]; then
        real="$(readlink -f "$dev" 2>/dev/null || true)"
        if [[ -n "$real" ]] && is_used_device "$real"; then
          continue
        fi
        echo "$dev"
        return 0
      fi
    done
    if [[ -n "$fallback_index" ]]; then
      mapfile -t detected < <(detect_usb_index0_devices)
      for dev in "${detected[@]}"; do
        real="$(readlink -f "$dev" 2>/dev/null || true)"
        if [[ -n "$real" ]] && ! is_used_device "$real"; then
          echo "$dev"
          return 0
        fi
      done
    fi
    sleep 1
  done
  return 1
}

echo "[mjpg] stopping old user-owned streamers if any"
pkill -u "$(id -u)" -f "mjpg_streamer.*input_uvc.so" 2>/dev/null || true
sleep 1

for i in "${!CAM_DEVICE_CANDIDATES[@]}"; do
  name="${CAM_NAMES[$i]}"
  candidates="${CAM_DEVICE_CANDIDATES[$i]}"
  port="${CAM_PORTS[$i]}"
  log="$LOG_DIR/${name}_${port}.log"
  pidfile="$STREAM_ROOT/${name}_${port}.pid"

  if ! dev="$(resolve_device "$candidates" "$i")"; then
    echo "[mjpg] skip $name: no candidate device found after ${CAMERA_WAIT_SEC}s"
    echo "[mjpg] candidates: $candidates"
    echo "[mjpg] detected USB index0 devices:"
    detect_usb_index0_devices | sed 's/^/[mjpg]   /'
    continue
  fi
  real_dev="$(readlink -f "$dev" 2>/dev/null || true)"
  [[ -n "$real_dev" ]] && USED_REAL_DEVICES+=("$real_dev")

  echo "[mjpg] start $name dev=$dev port=$port ${WIDTH}x${HEIGHT}@${FPS}"
  nohup "$MJPG_STREAMER" \
    -i "input_uvc.so -d $dev -r ${WIDTH}x${HEIGHT} -f ${FPS} -q ${QUALITY}" \
    -o "output_http.so -w $WWW_DIR -p $port" \
    >"$log" 2>&1 &
  echo "$!" >"$pidfile"
  sleep "$PER_CAMERA_DELAY_SEC"
done

echo
echo "[mjpg] URLs:"
echo "  global_left : http://$(hostname -I | awk '{print $1}'):8080/?action=stream"
echo "  usb_16mp    : http://$(hostname -I | awk '{print $1}'):8081/?action=stream"
echo "  global_right: http://$(hostname -I | awk '{print $1}'):8082/?action=stream"
echo
echo "[mjpg] status:"
ps -u "$(id -u)" -o pid,cmd | grep -E "mjpg_streamer.*input_uvc.so" | grep -v grep || true
