from __future__ import annotations

import argparse
import json
import socket
import sys
import time


def load_pygame():
    try:
        import pygame  # type: ignore
    except Exception as exc:
        print("[ERROR] pygame is required to read the controller.")
        print("Install with:")
        print(f"  {sys.executable} -m pip install pygame")
        print(f"Import error: {exc}")
        raise SystemExit(1)
    return pygame


def apply_deadzone(value: float, deadzone: float) -> float:
    if abs(value) < deadzone:
        return 0.0
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read a PC game controller and send its state to the Raspberry Pi dashboard over UDP."
    )
    parser.add_argument("--pi-host", default="192.168.0.218")
    parser.add_argument("--pi-port", type=int, default=8091)
    parser.add_argument("--rate-hz", type=float, default=20.0)
    parser.add_argument("--deadzone", type=float, default=0.05)
    parser.add_argument("--controller-index", type=int, default=0)
    args = parser.parse_args()

    pygame = load_pygame()
    pygame.init()
    pygame.joystick.init()

    count = pygame.joystick.get_count()
    if count <= 0:
        print("[ERROR] No controller found.")
        print("Check Windows game controller settings or reconnect the controller.")
        return 1

    if args.controller_index >= count:
        print(f"[ERROR] controller index {args.controller_index} is out of range. found={count}")
        return 1

    joystick = pygame.joystick.Joystick(args.controller_index)
    joystick.init()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    interval = 1.0 / max(args.rate_hz, 1.0)
    seq = 0

    print(f"[controller] using: {joystick.get_name()}")
    print(f"[udp] sending to {args.pi_host}:{args.pi_port} at {args.rate_hz:g} Hz")
    print("[RUN] Press Ctrl+C to stop.")

    try:
        while True:
            pygame.event.pump()

            axes = [
                round(apply_deadzone(float(joystick.get_axis(i)), args.deadzone), 4)
                for i in range(joystick.get_numaxes())
            ]
            buttons = [
                int(joystick.get_button(i))
                for i in range(joystick.get_numbuttons())
            ]
            hats = [
                list(joystick.get_hat(i))
                for i in range(joystick.get_numhats())
            ]

            payload = {
                "seq": seq,
                "pc_time": time.time(),
                "name": joystick.get_name(),
                "axes": axes,
                "buttons": buttons,
                "hats": hats,
            }
            raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            sock.sendto(raw, (args.pi_host, args.pi_port))

            pressed = [i for i, value in enumerate(buttons) if value]
            print(
                f"\rseq={seq} axes={axes} pressed={pressed} hats={hats}      ",
                end="",
                flush=True,
            )
            seq += 1
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[STOP]")
    finally:
        pygame.quit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
