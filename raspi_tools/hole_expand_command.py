#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time

import serial


def build_start_command(args: argparse.Namespace) -> str:
    parts = [
        "EXPAND",
        "START",
        f"dc={args.dc}",
        f"feed_steps={args.feed_steps}",
        f"retract_steps={args.retract_steps}",
        f"feed_hz={args.feed_hz}",
        f"spinup_ms={args.spinup_ms}",
        f"dwell_ms={args.dwell_ms}",
        f"pass_pause_ms={args.pass_pause_ms}",
        f"passes={args.passes}",
    ]
    return ",".join(parts)


def wait_lines(ser: serial.Serial, seconds: float) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        raw = ser.readline().decode(errors="replace").strip()
        if raw:
            print(raw)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send hole-expansion commands to ESP32 over Raspberry Pi UART."
    )
    parser.add_argument("--port", default="/dev/ttyAMA4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=0.25)
    parser.add_argument("--listen-s", type=float, default=5.0)

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start", action="store_true")
    mode.add_argument("--stop", action="store_true")
    mode.add_argument("--home", action="store_true")
    mode.add_argument("--status", action="store_true")

    parser.add_argument("--dc", type=int, default=70)
    parser.add_argument("--feed-steps", type=int, default=400)
    parser.add_argument("--retract-steps", type=int, default=400)
    parser.add_argument("--feed-hz", type=int, default=500)
    parser.add_argument("--spinup-ms", type=int, default=1000)
    parser.add_argument("--dwell-ms", type=int, default=300)
    parser.add_argument("--pass-pause-ms", type=int, default=200)
    parser.add_argument("--passes", type=int, default=1)

    args = parser.parse_args()

    if args.start:
        command = build_start_command(args)
    elif args.stop:
        command = "EXPAND,STOP"
    elif args.home:
        command = "EXPAND,HOME"
    else:
        command = "EXPAND,STATUS"

    with serial.Serial(args.port, args.baud, timeout=args.timeout) as ser:
        ser.reset_input_buffer()
        print(f"TX {command}")
        ser.write((command + "\n").encode())
        wait_lines(ser, args.listen_s)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
