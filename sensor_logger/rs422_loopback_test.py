"""
RS-422 adapter loopback test for DSD TECH SH-U11L.

Disconnect DL50 first, then wire the adapter terminal block like this:
    TxD+ -> RxD+
    TxD- -> RxD-

Run:
    python rs422_loopback_test.py --port COM10

Expected RX:
    02 30 31 30 37 03
which is:
    <STX>0107<ETX>
"""

from __future__ import annotations

import argparse
import time


REQUEST = b"\x020107\x03"


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def main() -> int:
    parser = argparse.ArgumentParser(description="RS-422 adapter loopback test.")
    parser.add_argument("--port", required=True, help="COM port, for example COM10.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()

    try:
        import serial
    except ImportError:
        print("pyserial is not installed. Install with: pip install pyserial")
        return 1

    with serial.Serial(
        port=args.port,
        baudrate=args.baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.3,
        write_timeout=0.3,
    ) as ser:
        print(f"Opened {args.port} at {args.baud} bps, 8N1")
        for i in range(args.count):
            ser.reset_input_buffer()
            ser.write(REQUEST)
            ser.flush()
            rx = ser.read(len(REQUEST))
            print(f"{i + 1}: TX={bytes_to_hex(REQUEST)} RX={bytes_to_hex(rx)}")
            time.sleep(0.5)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
