"""
Configure SICK DL50 Hi serial settings for the main logger.

Current observed setting:
    38400 bps, 7E1, continuous CRLF output

Target setting:
    115200 bps, 8N1, request mode / standard protocol

Run:
    python dl50_set_standard.py --port COM10

After this succeeds:
    python sensor_logger.py --mode dl50 --port COM10 --baud 115200 --bytesize 8 --parity N --stopbits 1 --count 10
"""

from __future__ import annotations

import argparse
import time


REQUEST_VALUE = b"\x020107\x03"
CONTINUOUS_OFF = b"\x02050200\x03"
SET_BAUD_115200 = b"\x02022B03\x03"
SET_PARITY_NONE = b"\x02022C00\x03"
ACTIVATE_AND_SAVE = b"\x020306\x03"


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def open_serial(port: str, baud: int, bytesize: int, parity: str):
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
        timeout=0.4,
        write_timeout=0.4,
    )


def send_command(ser, label: str, command: bytes) -> bytes:
    ser.reset_input_buffer()
    ser.write(command)
    ser.flush()
    time.sleep(0.15)
    response = ser.read(128)
    print(f"{label}: TX={bytes_to_hex(command)} RX={bytes_to_hex(response)}")
    print(f"{label}: ascii={response.decode('ascii', errors='replace')!r}")
    return response


def send_once(
    port: str,
    baud: int,
    bytesize: int,
    parity: str,
    label: str,
    command: bytes,
) -> bytes:
    print(f"Opening {port}, {baud} bps, {bytesize}{parity}1 for {label}")
    with open_serial(port, baud, bytesize, parity) as ser:
        return send_command(ser, label, command)


def send_with_fallbacks(port: str, candidates: list[tuple[int, int, str]], label: str, command: bytes) -> None:
    for baud, bytesize, parity in candidates:
        response = send_once(port, baud, bytesize, parity, label, command)
        if response:
            return


def main() -> int:
    parser = argparse.ArgumentParser(description="Set DL50 Hi to standard logger settings.")
    parser.add_argument("--port", required=True, help="COM port, for example COM10.")
    parser.add_argument("--current-baud", type=int, default=38400)
    parser.add_argument("--current-bytesize", type=int, choices=[7, 8], default=7)
    parser.add_argument("--current-parity", choices=["N", "E", "O"], default="E")
    args = parser.parse_args()

    try:
        import serial  # noqa: F401
    except ImportError:
        print("pyserial is not installed. Install with: pip install pyserial")
        return 1

    # Parameter changes can become active immediately. Therefore commands are
    # sent in stages and later commands are retried with plausible current and
    # target serial formats.
    send_once(
        args.port,
        args.current_baud,
        args.current_bytesize,
        args.current_parity,
        "continuous off",
        CONTINUOUS_OFF,
    )

    send_once(
        args.port,
        args.current_baud,
        args.current_bytesize,
        args.current_parity,
        "set parity none",
        SET_PARITY_NONE,
    )

    time.sleep(0.3)
    send_with_fallbacks(
        args.port,
        [
            (args.current_baud, 8, "N"),
            (args.current_baud, args.current_bytesize, args.current_parity),
        ],
        "set baud 115200",
        SET_BAUD_115200,
    )

    time.sleep(0.5)
    send_with_fallbacks(
        args.port,
        [
            (115200, 8, "N"),
            (args.current_baud, 8, "N"),
            (args.current_baud, args.current_bytesize, args.current_parity),
        ],
        "activate and save",
        ACTIVATE_AND_SAVE,
    )

    print("Waiting for DL50 to apply saved settings...")
    time.sleep(1.0)

    print("Verifying target settings: 115200 bps, 8N1")
    with open_serial(args.port, 115200, 8, "N") as ser:
        response = send_command(ser, "request value", REQUEST_VALUE)

    if b"\x02" in response and b"\x03" in response:
        print("Verification looks good: STX/ETX frame received.")
    else:
        print("No STX/ETX frame received. If the DL50 still streams CRLF, rerun probe tests.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
