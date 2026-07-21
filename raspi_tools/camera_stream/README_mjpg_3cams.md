# Raspberry Pi 3-camera MJPEG streaming

This setup runs three user-owned `mjpg_streamer` processes, one per camera,
and a watchdog that restarts them when snapshots fail.

## Camera layout

```text
global_left  -> port 8080
usb_16mp     -> port 8081
global_right -> port 8082
```

The start script uses stable `/dev/v4l/by-path` links and waits up to
`CAMERA_WAIT_SEC` seconds for cameras to appear.  This is important after boot
and after unplug/replug events.

As of 2026-07-20, the current observed USB mapping is:

```text
global_left  -> /dev/v4l/by-path/platform-xhci-hcd.1-usb-0:1:1.0-video-index0
usb_16mp     -> /dev/v4l/by-path/platform-xhci-hcd.0-usb-0:2:1.0-video-index0
global_right -> /dev/v4l/by-path/platform-xhci-hcd.1-usb-0:2:1.0-video-index0
```

The script also avoids assigning the same real `/dev/video*` device to two
ports.  This prevents a failure where port 8082 tried to open the camera that
was already used by port 8080.

Default stream setting:

```text
320x240, 5 fps, MJPEG quality 60
```

## Start and stop

Start camera streams, watchdog, and dashboard:

```bash
cd ~/pipe_robot_dev/camera_stream
./start_camera_dashboard.sh
```

Stop dashboard/watchdog:

```bash
cd ~/pipe_robot_dev/camera_stream
./stop_camera_dashboard.sh
```

Stop only camera streams:

```bash
cd ~/pipe_robot_dev/camera_stream
./stop_mjpg_3cams.sh
```

Check snapshots:

```bash
cd ~/pipe_robot_dev/camera_stream
./check_mjpg_3cams.sh
```

## Autostart on Raspberry Pi boot

Install the user cron entry once:

```bash
cd ~/pipe_robot_dev/camera_stream
./install_camera_autostart.sh
```

This adds:

```text
@reboot sleep 20; ~/pipe_robot_dev/camera_stream/start_camera_dashboard.sh
```

It does not require sudo.  Logs go to:

```text
~/pipe_robot_logs/camera_stream/autostart.log
~/pipe_robot_logs/camera_stream/camera_watchdog.log
~/pipe_robot_logs/camera_stream/camera_watchdog_restart.log
```

## URLs

```text
http://192.168.50.154:8080/?action=stream
http://192.168.50.154:8081/?action=stream
http://192.168.50.154:8082/?action=stream
http://192.168.50.154:8090/robot_dashboard.html
```

## Tuning

Lower load:

```bash
WIDTH=320 HEIGHT=240 FPS=5 ./start_camera_dashboard.sh
```

Higher quality:

```bash
WIDTH=640 HEIGHT=480 FPS=10 QUALITY=70 ./start_camera_dashboard.sh
```

## Boot/start stability notes

Three USB cameras can cause USB enumeration races and Raspberry Pi low-voltage
events at boot.  The start script therefore staggers camera startup:

```text
START_DELAY_SEC=20
PER_CAMERA_DELAY_SEC=5
```

You can override these if needed:

```bash
cd ~/pipe_robot_dev/camera_stream
START_DELAY_SEC=30 PER_CAMERA_DELAY_SEC=8 ./start_mjpg_3cams.sh
```

Check power warnings:

```bash
vcgencmd get_throttled
journalctl -b --no-pager | grep -iE 'under|voltage|usb|uvc'
```

Observed meanings:

```text
throttled=0x50000  -> undervoltage happened in the past, not currently active
throttled=0x50005  -> undervoltage is/was active; camera/USB instability is likely
```

If `0x50005` appears repeatedly with three cameras, prefer a powered USB hub or
a stronger 5 V supply path to the Raspberry Pi before increasing camera
resolution or frame rate.
