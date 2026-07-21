"""
Interactive laser angle limit checker for KRS-9004.

Use this to find the usable laser-angle range before running an automatic scan.

The script controls the angle-mode KRS-9004 servo, typically ID 5, through the
KONDO Dual USB Adapter. It shows the commanded angle and the corresponding ICS
position. KRS-9004 does not provide an absolute external angle measurement here;
the displayed angle is the command angle based on the configured mapping.

Examples:
    python laser_angle_limit_check.py --port COM8 --id 5
    python laser_angle_limit_check.py --port COM8 --id 5 --start-angle 90 --step-deg 1
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


def angle_to_position(angle_deg: float, min_deg: float, max_deg: float, min_pos: int, max_pos: int) -> int:
    clamped = max(min_deg, min(max_deg, angle_deg))
    ratio = (clamped - min_deg) / (max_deg - min_deg)
    return round(min_pos + ratio * (max_pos - min_pos))


def encode_position_command(servo_id: int, position: int) -> bytes:
    position = max(0, min(11500, int(position)))
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
    print(f"  ICS position={position} TX={bytes_to_hex(command)} RX={bytes_to_hex(response)}")
    return response


def send_angle(ser, args: argparse.Namespace, angle_deg: float) -> float:
    clamped = max(args.min_deg, min(args.max_deg, angle_deg))
    position = angle_to_position(clamped, args.min_deg, args.max_deg, args.min_pos, args.max_pos)
    print(f"[ANGLE] command={clamped:.3f} deg")
    send_position(ser, args.id, position)
    return clamped


def print_help() -> None:
    print()
    print("Commands:")
    print("  +        : angle += step")
    print("  -        : angle -= step")
    print("  ++       : angle += 5 * step")
    print("  --       : angle -= 5 * step")
    print("  g <deg>  : go to angle. Example: g 112")
    print("  s <deg>  : set step size. Example: s 0.5")
    print("  c        : center, 0 deg")
    print("  f        : servo free")
    print("  q        : center and quit")
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
        print(f"Opened {args.port} at {args.baud} bps, 8E1, servo ID={args.id}")
        print(f"Mapping: {args.min_deg:g} deg={args.min_pos}, 0 deg=7500, {args.max_deg:g} deg={args.max_pos}")
        print("レーザーが装置に当たりそうになったら、すぐ q または c を押してください。")
        print("まずは大きめの角度まで移動し、その後 step=1 や 0.5 deg で限界を探すのがおすすめです。")

        current_angle = send_angle(ser, args, args.start_angle)
        step = args.step_deg
        print_help()

        while True:
            text = input(f"[current={current_angle:.3f} deg, step={step:g}] > ").strip()
            if not text:
                continue
            parts = text.split()
            cmd = parts[0].lower()

            try:
                if cmd == "q":
                    send_angle(ser, args, 0.0)
                    break
                if cmd == "h":
                    print_help()
                elif cmd == "+":
                    current_angle = send_angle(ser, args, current_angle + step)
                elif cmd == "-":
                    current_angle = send_angle(ser, args, current_angle - step)
                elif cmd == "++":
                    current_angle = send_angle(ser, args, current_angle + 5.0 * step)
                elif cmd == "--":
                    current_angle = send_angle(ser, args, current_angle - 5.0 * step)
                elif cmd == "g" and len(parts) == 2:
                    current_angle = send_angle(ser, args, float(parts[1]))
                elif cmd == "s" and len(parts) == 2:
                    step = abs(float(parts[1]))
                    print(f"[STEP] step={step:g} deg")
                elif cmd == "c":
                    current_angle = send_angle(ser, args, 0.0)
                elif cmd == "f":
                    print("[FREE] servo free")
                    send_position(ser, args.id, 0)
                else:
                    print("Unknown command. Enter h for help.")
            except ValueError:
                print("Invalid number. Enter h for help.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Find usable laser servo angle range.")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--id", type=int, default=DEFAULT_SERVO_ID)
    parser.add_argument("--start-angle", type=float, default=0.0)
    parser.add_argument("--step-deg", type=float, default=5.0)
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
    if args.step_deg <= 0:
        print("--step-deg must be greater than zero.")
        return 2

    try:
        import serial  # noqa: F401
    except ImportError:
        print("pyserial is not installed. Install with: pip install pyserial")
        return 1

    try:
        run(args)
    except KeyboardInterrupt:
        print("\n[RUN] Ctrl+C received. Run the script again and send c if you need to center the servo.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
