#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import time
from dataclasses import dataclass

import serial

try:
    from evdev import InputDevice, categorize, ecodes, list_devices
except Exception as exc:  # pragma: no cover
    InputDevice = None
    categorize = None
    ecodes = None
    list_devices = None
    EVDEV_IMPORT_ERROR = exc
else:
    EVDEV_IMPORT_ERROR = None


DEFAULT_BUTTON_MAP = {
    "move_push": "BTN_THUMB",
    "move_pull": "BTN_THUMB2",
    "drill_push": "BTN_TOP",
    "drill_pull": "BTN_TOP2",
    "grinder_air": "BTN_BASE",
}

VALVE_COMMAND_NAMES = {
    "move_push": "MOVE_PUSH",
    "move_pull": "MOVE_PULL",
    "drill_push": "DRILL_PUSH",
    "drill_pull": "DRILL_PULL",
    "grinder_air": "GRINDER_AIR",
}


@dataclass
class ControllerState:
    axes: dict[str, int]
    buttons: dict[str, int]
    hats: dict[str, int]
    seq: int = 0


def find_controller(path: str | None) -> InputDevice:
    if InputDevice is None:
        raise RuntimeError(f"evdev import failed: {EVDEV_IMPORT_ERROR}")
    if path:
        return InputDevice(path)

    candidates = []
    for device_path in list_devices():
      dev = InputDevice(device_path)
      caps = dev.capabilities()
      if ecodes.EV_ABS in caps or ecodes.EV_KEY in caps:
          candidates.append(dev)
    if not candidates:
        raise RuntimeError("No joystick/controller device found under /dev/input")
    return candidates[0]


def code_name(code: int, event_type: int) -> str:
    name = ecodes.bytype.get(event_type, {}).get(code, str(code))
    if isinstance(name, list):
        return name[0]
    return str(name)


def parse_map(items: list[str]) -> dict[str, str]:
    mapping = dict(DEFAULT_BUTTON_MAP)
    for item in items:
        if "=" not in item:
            raise ValueError(f"Bad --map value: {item}")
        valve, button = item.split("=", 1)
        valve = valve.strip().lower()
        button = button.strip()
        if valve not in VALVE_COMMAND_NAMES:
            raise ValueError(f"Unknown valve name in --map: {valve}")
        mapping[valve] = button
    return mapping


def send_line(ser: serial.Serial, line: str) -> None:
    ser.write((line.strip() + "\n").encode("ascii"))
    ser.flush()


def send_command_udp(udp: socket.socket, target: tuple[str, int], line: str) -> None:
    payload = json.dumps({"lines": [line.strip()]}).encode("utf-8")
    udp.sendto(payload, target)


def publish_controller_state(
    udp: socket.socket,
    target: tuple[str, int],
    dev_name: str,
    state: ControllerState,
) -> None:
    payload = {
        "name": dev_name,
        "seq": state.seq,
        "axes": list(state.axes.values()),
        "buttons": list(state.buttons.values()),
        "pressed_buttons": [name for name, value in state.buttons.items() if value],
        "hats": list(state.hats.values()),
        "axis_names": list(state.axes.keys()),
        "button_names": list(state.buttons.keys()),
        "hat_names": list(state.hats.keys()),
    }
    udp.sendto(json.dumps(payload).encode("utf-8"), target)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Forward controller events to ESP2 and keep solenoid valves ON only while mapped buttons are held."
    )
    parser.add_argument("--device", help="Controller input device, e.g. /dev/input/event4")
    parser.add_argument("--esp-port", default="/dev/ttyAMA4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-udp-port", type=int, default=8091)
    parser.add_argument("--command-udp-port", type=int, default=8092)
    parser.add_argument("--direct-serial", action="store_true", help="Open ESP UART directly. Stop dashboard first.")
    parser.add_argument("--valve-period-s", type=float, default=0.1)
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="VALVE=BUTTON",
        help="Override button mapping, e.g. move_push=BTN_BASE2",
    )
    parser.add_argument("--print-events", action="store_true")
    args = parser.parse_args()

    button_map = parse_map(args.map)
    button_to_valves: dict[str, list[str]] = {}
    for valve, button in button_map.items():
        button_to_valves.setdefault(button, []).append(valve)

    dev = find_controller(args.device)
    print(f"Controller: {dev.path} {dev.name}")
    print("Valve button map:")
    for valve, button in button_map.items():
        print(f"  {button:>12s} -> {valve}")

    state = ControllerState(axes={}, buttons={}, hats={})
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_target = (args.dashboard_host, args.dashboard_udp_port)
    command_target = (args.dashboard_host, args.command_udp_port)
    last_valve_send = 0.0
    last_udp_send = 0.0

    ser = serial.Serial(args.esp_port, args.baud, timeout=0.02) if args.direct_serial else None

    def command(line: str) -> None:
        if ser is not None:
            send_line(ser, line)
        else:
            send_command_udp(udp, command_target, line)

    try:
        command("VALVE,ALL,0")
        for event in dev.read_loop():
            if event.type == ecodes.EV_ABS:
                name = code_name(event.code, event.type)
                state.axes[name] = int(event.value)
                command(f"AXIS,{name},{int(event.value)}")
            elif event.type == ecodes.EV_KEY:
                name = code_name(event.code, event.type)
                value = 1 if int(event.value) else 0
                state.buttons[name] = value
                command(f"BTN,{name},{value}")
                for valve in button_to_valves.get(name, []):
                    command(f"VALVE,{VALVE_COMMAND_NAMES[valve]},{value}")
            else:
                continue

            state.seq += 1
            now = time.monotonic()
            if args.print_events:
                print(event)

            if now - last_valve_send >= args.valve_period_s:
                for valve, button in button_map.items():
                    if state.buttons.get(button, 0):
                        command(f"VALVE,{VALVE_COMMAND_NAMES[valve]},1")
                last_valve_send = now

            if now - last_udp_send >= 0.05:
                publish_controller_state(udp, udp_target, dev.name, state)
                last_udp_send = now
    finally:
        if ser is not None:
            ser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
