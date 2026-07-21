#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from urllib.parse import urlencode
from urllib.request import urlopen

import serial


VALID_VALVES = {
    "move_push": "MOVE_PUSH",
    "move_pull": "MOVE_PULL",
    "drill_push": "DRILL_PUSH",
    "drill_pull": "DRILL_PULL",
    "grinder_air": "GRINDER_AIR",
}


def send_line(ser: serial.Serial, line: str) -> None:
    payload = (line.strip() + "\n").encode("ascii")
    ser.write(payload)
    ser.flush()
    print(f"TX {line.strip()}")


def send_http(base_url: str, valve: str, value: str) -> None:
    query = urlencode({"name": valve, "on": value})
    url = base_url.rstrip("/") + "/api/valve?" + query
    with urlopen(url, timeout=2.0) as res:
        payload = json.loads(res.read().decode("utf-8"))
    print(f"HTTP {valve}={value}: {payload}")


def read_available(ser: serial.Serial, duration_s: float = 0.2) -> None:
    end = time.monotonic() + duration_s
    while time.monotonic() < end:
        raw = ser.readline()
        if raw:
            print("RX", raw.decode(errors="replace").strip())


def main() -> int:
    parser = argparse.ArgumentParser(description="Send dead-man valve commands to ESP2.")
    parser.add_argument("--port", default="/dev/ttyAMA4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--dashboard-url", default="http://127.0.0.1:8090")
    parser.add_argument("--direct-serial", action="store_true", help="Open UART directly. Stop dashboard first.")
    parser.add_argument("--valve", choices=sorted(VALID_VALVES), help="Valve to operate.")
    parser.add_argument("--hold-s", type=float, default=0.5, help="Seconds to keep ON.")
    parser.add_argument("--period-s", type=float, default=0.1, help="ON command repeat period.")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--all-off", action="store_true")
    args = parser.parse_args()

    if not args.direct_serial:
        if args.status:
            send_http(args.dashboard_url, "STATUS", "0")
            return 0
        if args.all_off:
            send_http(args.dashboard_url, "ALL", "0")
            return 0
        if not args.valve:
            parser.error("--valve is required unless --status or --all-off is used")
        name = VALID_VALVES[args.valve]
        end = time.monotonic() + max(0.0, args.hold_s)
        while time.monotonic() < end:
            send_http(args.dashboard_url, name, "1")
            time.sleep(max(0.02, args.period_s))
        send_http(args.dashboard_url, name, "0")
        return 0

    with serial.Serial(args.port, args.baud, timeout=0.05) as ser:
        if args.status:
            send_line(ser, "VALVE,STATUS")
            read_available(ser, 0.5)
            return 0

        if args.all_off:
            send_line(ser, "VALVE,ALL,0")
            read_available(ser, 0.3)
            return 0

        if not args.valve:
            parser.error("--valve is required unless --status or --all-off is used")

        name = VALID_VALVES[args.valve]
        end = time.monotonic() + max(0.0, args.hold_s)
        while time.monotonic() < end:
            send_line(ser, f"VALVE,{name},1")
            read_available(ser, 0.02)
            time.sleep(max(0.02, args.period_s))
        send_line(ser, f"VALVE,{name},0")
        read_available(ser, 0.5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
