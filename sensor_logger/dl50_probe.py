"""
DL50 Hi RS-422 probe.

Use this after the RS-422 adapter loopback test passes.

Examples:
    python dl50_probe.py --port COM10 --read-idle
    python dl50_probe.py --port COM10 --request
    python dl50_probe.py --port COM10 --sweep
"""

from __future__ import annotations

import argparse
import time


REQUEST_VALUE = b"\x020107\x03"
CONTINUOUS_OFF = b"\x02050200\x03"
CONTINUOUS_ON = b"\x02050201\x03"
BAUDS = [115200, 57600, 38400, 19200]


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def show_bytes(label: str, data: bytes) -> None:
    ascii_preview = data.decode("ascii", errors="replace")
    print(f"{label} len={len(data)} hex={bytes_to_hex(data)} ascii={ascii_preview!r}")


def open_serial(port: str, baud: int, bytesize: int = 8, parity: str = "N"):
    import serial

    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize={7: serial.SEVENBITS, 8: serial.EIGHTBITS}[bytesize],
        parity={
            "N": serial.PARITY_NONE,
            "E": serial.PARITY_EVEN,
            "O": serial.PARITY_ODD,
        }[parity],
        stopbits=serial.STOPBITS_ONE,
        timeout=0.25,
        write_timeout=0.25,
    )


def read_idle(port: str, baud: int, seconds: float, bytesize: int = 8, parity: str = "N") -> None:
    with open_serial(port, baud, bytesize, parity) as ser:
        print(
            f"Opened {port} at {baud} bps, {bytesize}{parity}1. "
            f"Reading without TX for {seconds:g}s."
        )
        ser.reset_input_buffer()
        end_t = time.monotonic() + seconds
        while time.monotonic() < end_t:
            data = ser.read(128)
            if data:
                show_bytes("IDLE RX", data)


def send_and_read(
    port: str,
    baud: int,
    command: bytes,
    label: str,
    bytesize: int = 8,
    parity: str = "N",
) -> bytes:
    with open_serial(port, baud, bytesize, parity) as ser:
        print(f"Opened {port} at {baud} bps, {bytesize}{parity}1. Sending {label}.")
        ser.reset_input_buffer()
        ser.write(command)
        ser.flush()
        time.sleep(0.05)
        data = ser.read(128)
        show_bytes("RX", data)
        return data


def sweep(port: str) -> None:
    for baud in BAUDS:
        print()
        data = send_and_read(port, baud, REQUEST_VALUE, f"request value at {baud}")
        if b"\x02" in data and b"\x03" in data:
            print(f"Possible frame found at {baud} bps.")


def main() -> int:
    parser = argparse.ArgumentParser(description="DL50 Hi serial probe.")
    parser.add_argument("--port", required=True, help="COM port, for example COM10.")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--bytesize", type=int, choices=[7, 8], default=8)
    parser.add_argument("--parity", choices=["N", "E", "O"], default="N")
    parser.add_argument("--read-idle", action="store_true")
    parser.add_argument("--idle-sweep", action="store_true")
    parser.add_argument("--request", action="store_true")
    parser.add_argument("--continuous-off", action="store_true")
    parser.add_argument("--continuous-on", action="store_true")
    parser.add_argument("--sweep", action="store_true")
    args = parser.parse_args()

    try:
        import serial  # noqa: F401
    except ImportError:
        print("pyserial is not installed. Install with: pip install pyserial")
        return 1

    if args.read_idle:
        read_idle(args.port, args.baud, 2.0, args.bytesize, args.parity)
    if args.idle_sweep:
        for baud in BAUDS:
            print()
            read_idle(args.port, baud, 1.0, args.bytesize, args.parity)
    if args.continuous_off:
        send_and_read(
            args.port,
            args.baud,
            CONTINUOUS_OFF,
            "continuous off",
            args.bytesize,
            args.parity,
        )
    if args.continuous_on:
        send_and_read(
            args.port,
            args.baud,
            CONTINUOUS_ON,
            "continuous on",
            args.bytesize,
            args.parity,
        )
    if args.request:
        send_and_read(
            args.port,
            args.baud,
            REQUEST_VALUE,
            "request value",
            args.bytesize,
            args.parity,
        )
    if args.sweep:
        sweep(args.port)

    if not any(
        [
            args.read_idle,
            args.idle_sweep,
            args.continuous_off,
            args.continuous_on,
            args.request,
            args.sweep,
        ]
    ):
        parser.print_help()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
