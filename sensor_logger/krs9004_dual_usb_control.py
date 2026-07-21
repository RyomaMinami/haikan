"""
KONDO KRS-9004 control via Dual USB Adapter HS.

This uses the adapter as a serial COM port and sends ICS position commands.
Close ICS Manager before running this script because only one program can open
the COM port at a time.

For rotation mode servos:
    7500 is neutral/stop
    values above/below 7500 rotate in opposite directions

For angle mode servos:
    values such as 6000, 7500, 9000 move to positions
"""

from __future__ import annotations

import argparse
import time


DEFAULT_PORT = "COM8"
DEFAULT_BAUD = 115200
DEFAULT_SERVO_ID = 5


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
    print(f"target={position} TX={bytes_to_hex(command)} RX={bytes_to_hex(response)}")
    return response


def interactive(port: str, baud: int, servo_id: int) -> None:
    import serial

    with serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.1,
        write_timeout=0.1,
    ) as ser:
        print(f"Opened {port} at {baud} bps, 8E1, servo ID={servo_id}")
        print("Commands:")
        print("  s : stop / neutral 7500")
        print("  + : slow + 7600")
        print("  - : slow - 7400")
        print("  1 : stronger + 8000")
        print("  2 : stronger - 7000")
        print("  c : center 7500")
        print("  l : angle/left 6000")
        print("  r : angle/right 9000")
        print("  f : free 0")
        print("  q : quit")
        send_position(ser, servo_id, 7500)

        while True:
            key = input("> ").strip()
            if key.lower() == "q":
                send_position(ser, servo_id, 7500)
                break
            if key.lower() in ("s", "c"):
                send_position(ser, servo_id, 7500)
            elif key == "+":
                send_position(ser, servo_id, 7600)
            elif key == "-":
                send_position(ser, servo_id, 7400)
            elif key == "1":
                send_position(ser, servo_id, 8000)
            elif key == "2":
                send_position(ser, servo_id, 7000)
            elif key.lower() == "l":
                send_position(ser, servo_id, 6000)
            elif key.lower() == "r":
                send_position(ser, servo_id, 9000)
            elif key.lower() == "f":
                send_position(ser, servo_id, 0)
            else:
                try:
                    send_position(ser, servo_id, int(key))
                except ValueError:
                    print("Unknown command. Enter s, +, -, 1, 2, l, r, f, q, or a number.")


def main() -> int:
    parser = argparse.ArgumentParser(description="KRS-9004 Dual USB Adapter control.")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--id", type=int, default=DEFAULT_SERVO_ID)
    args = parser.parse_args()

    try:
        import serial  # noqa: F401
    except ImportError:
        print("pyserial is not installed. Install with: pip install pyserial")
        return 1

    interactive(args.port, args.baud, args.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
