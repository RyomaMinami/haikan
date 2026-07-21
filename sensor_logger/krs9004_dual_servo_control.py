"""
Control two KRS-9004 servos on the same KONDO Dual USB Adapter.

Purpose:
    - ID 5: angle-mode servo that rotates the LK-G85A laser head.
    - ID 4: endless-rotation servo that drives an axial wheel.

Close ICS Manager before running this script.

ICS notes:
    The adapter is a serial COM port, typically 115200 bps, 8E1.
    Commands are sent sequentially, but the delay is only a few milliseconds,
    so this is effectively simultaneous for this experiment.

Rotation servo notes:
    7500 is neutral/stop for endless-rotation mode.
    Values above/below 7500 rotate in opposite directions.
    Start with small values such as 7600 or 7400 before using larger speeds.

Examples:
    python krs9004_dual_servo_control.py --port COM3
    python krs9004_dual_servo_control.py --port COM3 --laser-angle 30 --wheel-speed 7600
"""

from __future__ import annotations

import argparse
import time


DEFAULT_PORT = "COM3"
DEFAULT_BAUD = 115200
DEFAULT_LASER_ID = 5
DEFAULT_WHEEL_ID = 4
DEFAULT_MIN_DEG = -135.0
DEFAULT_MAX_DEG = 135.0
DEFAULT_MIN_POS = 3500
DEFAULT_MAX_POS = 11500
STOP_POSITION = 7500
FREE_POSITION = 0


def angle_to_position(
    angle_deg: float,
    min_deg: float,
    max_deg: float,
    min_pos: int,
    max_pos: int,
) -> int:
    clamped = max(min_deg, min(max_deg, angle_deg))
    ratio = (clamped - min_deg) / (max_deg - min_deg)
    return round(min_pos + ratio * (max_pos - min_pos))


def encode_position_command(servo_id: int, position: int) -> bytes:
    position = max(0, min(11500, position))
    return bytes(
        [
            0x80 | (servo_id & 0x1F),
            (position >> 7) & 0x7F,
            position & 0x7F,
        ]
    )


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def send_position(ser, servo_id: int, position: int, label: str) -> bytes:
    command = encode_position_command(servo_id, position)
    ser.reset_input_buffer()
    ser.write(command)
    ser.flush()
    time.sleep(0.015)
    response = ser.read(16)
    print(f"[{label}] id={servo_id} position={position} TX={bytes_to_hex(command)} RX={bytes_to_hex(response)}")
    return response


def send_laser_angle(ser, args: argparse.Namespace, angle_deg: float) -> int:
    position = angle_to_position(
        angle_deg,
        args.min_deg,
        args.max_deg,
        args.min_pos,
        args.max_pos,
    )
    print(f"[LASER] angle={angle_deg:.3f} deg -> position={position}")
    send_position(ser, args.laser_id, position, "LASER")
    return position


def send_wheel_speed(ser, args: argparse.Namespace, wheel_position: int) -> int:
    wheel_position = max(0, min(11500, int(wheel_position)))
    send_position(ser, args.wheel_id, wheel_position, "WHEEL")
    if wheel_position == STOP_POSITION:
        print("[WHEEL] stop / neutral")
    elif wheel_position == FREE_POSITION:
        print("[WHEEL] free")
    elif wheel_position > STOP_POSITION:
        print(f"[WHEEL] + direction speed command: +{wheel_position - STOP_POSITION}")
    else:
        print(f"[WHEEL] - direction speed command: -{STOP_POSITION - wheel_position}")
    return wheel_position


def send_both(
    ser,
    args: argparse.Namespace,
    angle_deg: float,
    wheel_position: int,
) -> tuple[int, int]:
    laser_position = angle_to_position(
        angle_deg,
        args.min_deg,
        args.max_deg,
        args.min_pos,
        args.max_pos,
    )
    wheel_position = max(0, min(11500, int(wheel_position)))
    print(
        f"[BOTH] laser angle={angle_deg:.3f} deg position={laser_position}, "
        f"wheel position={wheel_position}"
    )
    send_position(ser, args.laser_id, laser_position, "LASER")
    send_position(ser, args.wheel_id, wheel_position, "WHEEL")
    return laser_position, wheel_position


def print_help() -> None:
    print()
    print("Commands:")
    print("  a <deg>          : laser angle. Example: a 30")
    print("  w <pos>          : wheel speed position. 7500=stop, 7600=slow+, 7400=slow-")
    print("  b <deg> <pos>    : move laser and wheel together. Example: b 30 7600")
    print("  +                : wheel slow + 7600")
    print("  -                : wheel slow - 7400")
    print("  ++               : wheel stronger + 8000")
    print("  --               : wheel stronger - 7000")
    print("  s                : wheel stop 7500")
    print("  c                : laser center 0 deg and wheel stop")
    print("  f                : free both servos")
    print("  q                : stop wheel, center laser, quit")
    print()


def run(args: argparse.Namespace) -> None:
    import serial

    with serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.1,
        write_timeout=0.1,
    ) as ser:
        print(f"Opened {args.port} at {args.baud} bps, 8E1")
        print(f"Laser angle servo: ID {args.laser_id}")
        print(f"Wheel endless-rotation servo: ID {args.wheel_id}")
        print("安全確認: 車輪が浮いている、または配管内で動いても問題ない状態で実行してください。")
        print("最初は 7600 / 7400 程度の小さい速度から確認してください。")

        current_angle = 0.0
        current_wheel = STOP_POSITION

        send_both(ser, args, current_angle, current_wheel)

        if args.laser_angle is not None or args.wheel_speed is not None:
            current_angle = args.laser_angle if args.laser_angle is not None else current_angle
            current_wheel = args.wheel_speed if args.wheel_speed is not None else current_wheel
            send_both(ser, args, current_angle, current_wheel)
            if args.run_seconds is not None:
                print(f"[RUN] keep wheel command for {args.run_seconds:.3f} s")
                time.sleep(args.run_seconds)
                send_wheel_speed(ser, args, STOP_POSITION)
            return

        print_help()
        while True:
            text = input("> ").strip()
            if not text:
                continue
            parts = text.split()
            cmd = parts[0].lower()

            try:
                if cmd == "q":
                    send_both(ser, args, 0.0, STOP_POSITION)
                    break
                if cmd == "h":
                    print_help()
                elif cmd == "a" and len(parts) == 2:
                    current_angle = float(parts[1])
                    send_laser_angle(ser, args, current_angle)
                elif cmd == "w" and len(parts) == 2:
                    current_wheel = int(parts[1])
                    send_wheel_speed(ser, args, current_wheel)
                elif cmd == "b" and len(parts) == 3:
                    current_angle = float(parts[1])
                    current_wheel = int(parts[2])
                    send_both(ser, args, current_angle, current_wheel)
                elif cmd == "+":
                    current_wheel = 7600
                    send_wheel_speed(ser, args, current_wheel)
                elif cmd == "-":
                    current_wheel = 7400
                    send_wheel_speed(ser, args, current_wheel)
                elif cmd == "++":
                    current_wheel = 8000
                    send_wheel_speed(ser, args, current_wheel)
                elif cmd == "--":
                    current_wheel = 7000
                    send_wheel_speed(ser, args, current_wheel)
                elif cmd == "s":
                    current_wheel = STOP_POSITION
                    send_wheel_speed(ser, args, current_wheel)
                elif cmd == "c":
                    current_angle = 0.0
                    current_wheel = STOP_POSITION
                    send_both(ser, args, current_angle, current_wheel)
                elif cmd == "f":
                    send_position(ser, args.laser_id, FREE_POSITION, "LASER")
                    send_position(ser, args.wheel_id, FREE_POSITION, "WHEEL")
                else:
                    print("Unknown command. Enter h for help.")
            except ValueError:
                print("Invalid number. Enter h for help.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control laser angle servo and wheel rotation servo together.")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--laser-id", type=int, default=DEFAULT_LASER_ID)
    parser.add_argument("--wheel-id", type=int, default=DEFAULT_WHEEL_ID)
    parser.add_argument("--laser-angle", type=float, default=None, help="One-shot laser angle command.")
    parser.add_argument("--wheel-speed", type=int, default=None, help="One-shot wheel position command; 7500=stop.")
    parser.add_argument("--run-seconds", type=float, default=None, help="Stop wheel after this many seconds in one-shot mode.")
    parser.add_argument("--min-deg", type=float, default=DEFAULT_MIN_DEG)
    parser.add_argument("--max-deg", type=float, default=DEFAULT_MAX_DEG)
    parser.add_argument("--min-pos", type=int, default=DEFAULT_MIN_POS)
    parser.add_argument("--max-pos", type=int, default=DEFAULT_MAX_POS)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.min_deg >= args.max_deg:
        print("--min-deg must be smaller than --max-deg.")
        return 2
    if args.wheel_speed is not None and not 0 <= args.wheel_speed <= 11500:
        print("--wheel-speed must be between 0 and 11500.")
        return 2
    if args.run_seconds is not None and args.run_seconds < 0:
        print("--run-seconds must be >= 0.")
        return 2

    try:
        import serial  # noqa: F401
    except ImportError:
        print("pyserial is not installed. Install with: pip install pyserial")
        return 1

    try:
        run(args)
    except KeyboardInterrupt:
        print("\n[RUN] Ctrl+C received. If the wheel is still moving, run with --wheel-speed 7500.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
