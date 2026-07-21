#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import urllib.request


X_CENTER = 512
X_RANGE_HALF = 511
X_DEAD = 20
STEP_HZ_MIN = 200
STEP_HZ_MAX = 2000


HELP = """
Stepper keyboard jog

Keys:
  d / Right    start forward continuous rotation
  a / Left     start reverse continuous rotation
  Space / s    stop
  + / =        increase speed
  - / _        decrease speed
  ]            increase jog step count
  [            decrease jog step count
  k / Up       forward jog by the current step count
  j / Down     reverse jog by the current step count
  h / ?        show this help
  q / Esc      stop and quit

Notes:
  - This sends AXIS,ABS_X commands to the dashboard command UDP port.
  - The dashboard server must be running on the Raspberry Pi.
  - Jog steps are time-estimated from steps / Hz because the current ESP
    firmware exposes continuous AXIS control, not a direct STEP,N command.
"""


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def hz_to_abs_x(hz: int, direction: int) -> int:
    """Convert target step frequency and direction into ESP joystick ABS_X."""
    hz = clamp(abs(hz), STEP_HZ_MIN, STEP_HZ_MAX)
    span = STEP_HZ_MAX - STEP_HZ_MIN
    abs_off = int(round(X_DEAD + (hz - STEP_HZ_MIN) * (X_RANGE_HALF - X_DEAD) / span))
    # ESP firmware computes velocity from X_CENTER - raw.
    if direction > 0:
        return clamp(X_CENTER - abs_off, 0, 1023)
    return clamp(X_CENTER + abs_off, 0, 1023)


class CommandSender:
    def __init__(self, host: str, port: int, abs_y: int) -> None:
        self.target = (host, port)
        self.abs_y = abs_y
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_lines(self, lines: list[str]) -> None:
        payload = json.dumps({"lines": lines}, separators=(",", ":")).encode("utf-8")
        self.sock.sendto(payload, self.target)

    def axis_x(self, abs_x: int) -> None:
        self.send_lines([f"AXIS,ABS_Y,{self.abs_y}", f"AXIS,ABS_X,{abs_x}"])

    def stop(self) -> None:
        self.axis_x(X_CENTER)

    def close(self) -> None:
        self.sock.close()


def check_dashboard(host: str, http_port: int, timeout_s: float) -> None:
    url = f"http://{host}:{http_port}/api/state"
    try:
        with urllib.request.urlopen(url, timeout=timeout_s) as res:
            if res.status == 200:
                print(f"[OK] dashboard API: {url}")
                return
            print(f"[WARN] dashboard API status={res.status}: {url}")
    except Exception as exc:
        print(f"[WARN] dashboard API check failed: {url}")
        print(f"       {exc}")
        print("       Start the Raspberry Pi dashboard first if commands do not work.")


def read_key_windows() -> str:
    import msvcrt

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        code = msvcrt.getwch()
        return {
            "K": "LEFT",
            "M": "RIGHT",
            "H": "UP",
            "P": "DOWN",
        }.get(code, "")
    if ch == "\x1b":
        return "ESC"
    return ch


def read_key_posix() -> str:
    import termios
    import tty

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            seq = sys.stdin.read(2)
            return {
                "[D": "LEFT",
                "[C": "RIGHT",
                "[A": "UP",
                "[B": "DOWN",
            }.get(seq, "ESC")
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def read_key() -> str:
    if sys.platform.startswith("win"):
        return read_key_windows()
    return read_key_posix()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PC keyboard jog control for the pipe robot stepper motor."
    )
    parser.add_argument(
        "--pi-host",
        default="192.168.50.154",
        help="Raspberry Pi IP. Use 192.168.0.218 for direct wired LAN.",
    )
    parser.add_argument("--command-port", type=int, default=8092)
    parser.add_argument("--http-port", type=int, default=8090)
    parser.add_argument("--speed-hz", type=int, default=500)
    parser.add_argument("--speed-step-hz", type=int, default=100)
    parser.add_argument("--jog-steps", type=int, default=50)
    parser.add_argument("--jog-step-size", type=int, default=10)
    parser.add_argument("--abs-y", type=int, default=512, help="DC motor neutral command.")
    parser.add_argument("--repeat", type=int, default=3, help="repeat each command packet")
    parser.add_argument("--no-api-check", action="store_true")
    args = parser.parse_args()

    speed_hz = clamp(args.speed_hz, STEP_HZ_MIN, STEP_HZ_MAX)
    jog_steps = max(1, args.jog_steps)
    sender = CommandSender(args.pi_host, args.command_port, args.abs_y)

    if not args.no_api_check:
        check_dashboard(args.pi_host, args.http_port, timeout_s=2.0)

    print(HELP)
    print(f"[target] UDP {args.pi_host}:{args.command_port}")
    print(f"[state] speed={speed_hz} Hz, jog_steps={jog_steps}")
    print("[safety] Space stops the stepper. q/Esc stops and exits.")

    def send_axis_repeated(abs_x: int) -> None:
        for _ in range(max(1, args.repeat)):
            sender.axis_x(abs_x)
            time.sleep(0.02)

    def stop() -> None:
        send_axis_repeated(X_CENTER)
        print(f"\r[STOP] speed={speed_hz} Hz, jog_steps={jog_steps}        ")

    try:
        stop()
        while True:
            key = read_key()
            if not key:
                continue
            low = key.lower()

            if key in ("ESC",) or low == "q":
                stop()
                print("[QUIT]")
                return 0

            if low in ("h", "?"):
                print(HELP)
                continue

            if key in (" ",) or low == "s":
                stop()
                continue

            if low in ("+", "="):
                speed_hz = clamp(speed_hz + args.speed_step_hz, STEP_HZ_MIN, STEP_HZ_MAX)
                print(f"\r[speed] {speed_hz} Hz                          ")
                continue

            if low in ("-", "_"):
                speed_hz = clamp(speed_hz - args.speed_step_hz, STEP_HZ_MIN, STEP_HZ_MAX)
                print(f"\r[speed] {speed_hz} Hz                          ")
                continue

            if low == "]":
                jog_steps = max(1, jog_steps + args.jog_step_size)
                print(f"\r[jog_steps] {jog_steps}                         ")
                continue

            if low == "[":
                jog_steps = max(1, jog_steps - args.jog_step_size)
                print(f"\r[jog_steps] {jog_steps}                         ")
                continue

            if low == "d" or key == "RIGHT":
                abs_x = hz_to_abs_x(speed_hz, direction=1)
                send_axis_repeated(abs_x)
                print(f"\r[FWD continuous] speed={speed_hz} Hz abs_x={abs_x}        ")
                continue

            if low == "a" or key == "LEFT":
                abs_x = hz_to_abs_x(speed_hz, direction=-1)
                send_axis_repeated(abs_x)
                print(f"\r[REV continuous] speed={speed_hz} Hz abs_x={abs_x}        ")
                continue

            if low == "k" or key == "UP":
                abs_x = hz_to_abs_x(speed_hz, direction=1)
                duration_s = jog_steps / float(speed_hz)
                send_axis_repeated(abs_x)
                time.sleep(duration_s)
                stop()
                print(f"\r[FWD jog] {jog_steps} steps approx, {speed_hz} Hz        ")
                continue

            if low == "j" or key == "DOWN":
                abs_x = hz_to_abs_x(speed_hz, direction=-1)
                duration_s = jog_steps / float(speed_hz)
                send_axis_repeated(abs_x)
                time.sleep(duration_s)
                stop()
                print(f"\r[REV jog] {jog_steps} steps approx, {speed_hz} Hz        ")
                continue

    except KeyboardInterrupt:
        print("\n[Ctrl+C]")
        return 130
    finally:
        try:
            stop()
        finally:
            sender.close()


if __name__ == "__main__":
    raise SystemExit(main())
