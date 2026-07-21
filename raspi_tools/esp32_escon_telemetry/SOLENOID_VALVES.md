# Solenoid valve control notes

Date: 2026-07-12

This firmware supports dead-man control for five logical solenoid outputs:

- `MOVE_PUSH`: mobile-body SY3320-5LZ-C4 push
- `MOVE_PULL`: mobile-body SY3320-5LZ-C4 retract
- `DRILL_PUSH`: drilling SY3320-5LZ-C4 push
- `DRILL_PULL`: drilling SY3320-5LZ-C4 retract
- `GRINDER_AIR`: VXZ232 grinder air ON/OFF

## Safety behavior

Valve commands are hold-to-run.

- While a button is held, the Raspberry Pi bridge repeatedly sends `VALVE,<name>,1`.
- When the button is released, it sends `VALVE,<name>,0`.
- If commands stop unexpectedly, the ESP32 turns the valve OFF after `VALVE_HOLD_TIMEOUT_MS`.
- `MOVE_PUSH` and `MOVE_PULL` are interlocked so they cannot be ON together.
- `DRILL_PUSH` and `DRILL_PULL` are interlocked so they cannot be ON together.
- Emergency stop turns all valves OFF.

## ESP32 commands

Send commands to ESP2 UART, usually `/dev/ttyAMA4` on the Raspberry Pi:

```text
VALVE,MOVE_PUSH,1
VALVE,MOVE_PUSH,0
VALVE,MOVE_PULL,1
VALVE,DRILL_PUSH,1
VALVE,DRILL_PULL,1
VALVE,GRINDER_AIR,1
VALVE,ALL,0
VALVE,STATUS
```

## Current pin assignment

The pin constants are in `config.h`:

```cpp
PIN_VALVE_MOVE_PUSH
PIN_VALVE_MOVE_PULL
PIN_VALVE_DRILL_PUSH
PIN_VALVE_DRILL_PULL
PIN_VALVE_GRINDER_AIR
```

They are currently set to `-1` until the actual GPIO-to-valve-driver wiring is verified.  With `-1`, commands are rejected with `reason=pin_unset` and no GPIO is toggled.

The EAGLE files show solenoid output driver circuits, but the exact mapping to the active ESP32 firmware was not confirmed in software:

- Main ESP board appears to have three solenoid output connectors.
- Sensing board files include five solenoid output channels.

Before enabling physical output, verify continuity from the ESP32 GPIO pin to the TLP592A input resistor for each valve driver.

## Raspberry Pi helper commands

Manual pulse through the dashboard server.  This avoids opening `/dev/ttyAMA4`
from multiple processes at the same time:

```bash
cd ~/pipe_robot_dev
python3 valve_command.py --status
python3 valve_command.py --valve move_push --hold-s 0.5
python3 valve_command.py --all-off
```

Direct UART mode exists only for debugging.  Stop the dashboard first if using
it:

```bash
python3 valve_command.py --direct-serial --status
```

Controller bridge:

```bash
cd ~/pipe_robot_dev
python3 valve_controller_bridge.py --esp-port /dev/ttyAMA4
```

By default, the bridge sends commands to the dashboard command UDP port
`127.0.0.1:8092`, and the dashboard server writes them to ESP2 UART.  This is
the normal operating mode because the dashboard also reads the ESP2 telemetry.
Use `--direct-serial` only when the dashboard is stopped.

Default button mapping:

```text
BTN_THUMB  -> move_push
BTN_THUMB2 -> move_pull
BTN_TOP    -> drill_push
BTN_TOP2   -> drill_pull
BTN_BASE   -> grinder_air
```

Override examples:

```bash
python3 valve_controller_bridge.py --map move_push=BTN_BASE2 --map grinder_air=BTN_TRIGGER_HAPPY1
```

## Dashboard

`camera_stream/dashboard_server.py` reads the `TEL,...` line from ESP2 and splits `valve_*` telemetry into the `valves` section of `/api/state`.

`camera_stream/robot_dashboard.html` has a Solenoid valves panel showing:

- mobile push/retract
- drilling push/retract
- grinder air ON/OFF
- active valve bit mask

Dashboard also accepts valve commands:

```text
http://<raspi-ip>:8090/api/valve?name=MOVE_PUSH&on=1
http://<raspi-ip>:8090/api/valve?name=MOVE_PUSH&on=0
http://<raspi-ip>:8090/api/valve?name=ALL&on=0
```
