"""
KONDO KRS-9004 angle control via Dual USB Adapter HS.

Close ICS Manager before running this script.

Default mapping:
    -135 deg -> ICS position 3500
       0 deg -> ICS position 7500
    +135 deg -> ICS position 11500

Examples:
    python krs9004_angle_control.py --port COM8 --id 5
    python krs9004_angle_control.py --port COM8 --id 5 --angle 30
"""

from __future__ import annotations

import argparse
import time


DEFAULT_PORT = "COM8"
DEFAULT_BAUD = 115200
DEFAULT_SERVO_ID = 5
DEFAULT_MIN_DEG = -135.0
DEFAULT_MAX_DEG = 135.0
DEFAULT_MIN_POS = 3500
DEFAULT_MAX_POS = 11500


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


def send_position(ser, servo_id: int, position: int) -> bytes:
    command = encode_position_command(servo_id, position)
    ser.reset_input_buffer()
    ser.write(command)
    ser.flush()
    time.sleep(0.02)
    response = ser.read(16)
    print(f"position={position} TX={bytes_to_hex(command)} RX={bytes_to_hex(response)}")
    return response


def send_angle(
    ser,
    servo_id: int,
    angle_deg: float,
    min_deg: float,
    max_deg: float,
    min_pos: int,
    max_pos: int,
) -> None:
    position = angle_to_position(angle_deg, min_deg, max_deg, min_pos, max_pos)
    print(f"angle={angle_deg:.3f} deg -> position={position}")
    send_position(ser, servo_id, position)


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
        print(f"Opened {args.port} at {args.baud} bps, 8E1, servo ID={args.id}")
        print(
            f"Mapping: {args.min_deg:g} deg={args.min_pos}, "
            f"0 deg=7500, {args.max_deg:g} deg={args.max_pos}"
        )

        if args.angle is not None:
            send_angle(
                ser,
                args.id,
                args.angle,
                args.min_deg,
                args.max_deg,
                args.min_pos,
                args.max_pos,
            )
            return

        print("Enter angle in degrees. Examples: 0, 30, -45")
        print("Commands: c=center, f=free, q=quit")
        while True:
            text = input("> ").strip()
            if text.lower() == "q":
                break
            if text.lower() == "c":
                send_angle(
                    ser,
                    args.id,
                    0.0,
                    args.min_deg,
                    args.max_deg,
                    args.min_pos,
                    args.max_pos,
                )
                continue
            if text.lower() == "f":
                send_position(ser, args.id, 0)
                continue

            try:
                angle = float(text)
            except ValueError:
                print("Enter a number, c, f, or q.")
                continue

            send_angle(
                ser,
                args.id,
                angle,
                args.min_deg,
                args.max_deg,
                args.min_pos,
                args.max_pos,
            )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Move KRS-9004 by angle.")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--id", type=int, default=DEFAULT_SERVO_ID)
    parser.add_argument("--angle", type=float, default=None, help="Move once to this angle.")
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

    try:
        import serial  # noqa: F401
    except ImportError:
        print("pyserial is not installed. Install with: pip install pyserial")
        return 1

    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
