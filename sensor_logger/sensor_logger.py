"""
Minimal logger for KEYENCE LK-G85A/LK-G3000 and SICK DL50 Hi.

File layout expected:
    sensor_logger/
      sensor_logger.py
      LkIF.dll
      KeyUsbDrv.dll

Important:
    Python and LkIF.dll must have the same bitness.
    Example: 32-bit LkIF.dll needs 32-bit Python. 64-bit Python cannot load a
    32-bit DLL and usually raises:
        OSError: [WinError 193] %1 is not a valid Win32 application
"""

from __future__ import annotations

import argparse
import csv
import ctypes
import os
import platform
import re
import time
from ctypes import byref
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


# =========================
# User settings
# =========================

DL50_PORT = "COM3"
DL50_BAUDRATE = 115200
DL50_BYTESIZE = 8
DL50_PARITY = "N"
DL50_STOPBITS = 1
DL50_TIMEOUT_S = 0.2
DL50_MAX_RESPONSE_BYTES = 128
DL50_REQUEST = b"\x020107\x03"  # <STX>0107<ETX>

SAMPLE_PERIOD_S = 0.1
CSV_PATH = "sensor_log.csv"

LK_DLL_NAME = "LkIF.dll"


CSV_COLUMNS = [
    "pc_time",
    "elapsed_s",
    "lk_out1_mm",
    "lk_out2_mm",
    "lk_out1_status",
    "lk_out2_status",
    "dl50_hi_mm",
    "dl50_raw",
]


class LkFloatValue(ctypes.Structure):
    _fields_ = [
        ("FloatResult", ctypes.c_int),
        ("Value", ctypes.c_float),
    ]


LK_STATUS_NAMES = {
    0: "VALID",
    1: "RANGEOVER_N",
    2: "WAITING",
    3: "RANGEOVER_P",
    4: "ALARM",
}


@dataclass
class LkReading:
    out1_mm: Optional[float] = None
    out2_mm: Optional[float] = None
    out1_status: str = "not_used"
    out2_status: str = "not_used"


@dataclass
class Dl50Reading:
    mm: Optional[float] = None
    raw: str = ""


class LkG3000:
    def __init__(self, dll_path: Path) -> None:
        self.dll_path = dll_path
        self.dll: Optional[ctypes.WinDLL] = None
        self.available = False

    def open(self) -> None:
        if os.name != "nt":
            print("[LK] This DLL logger runs only on Windows.")
            return

        if not self.dll_path.exists():
            print(f"[LK] DLL not found: {self.dll_path}")
            return

        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(self.dll_path.parent))
            self.dll = ctypes.WinDLL(str(self.dll_path))
        except OSError as exc:
            print(f"[LK] Failed to load {self.dll_path}: {exc}")
            print(
                "[LK] Check that Python bitness matches LkIF.dll bitness "
                f"(current Python: {platform.architecture()[0]})."
            )
            return

        try:
            get_calc_data = self.dll.LKIF_GetCalcData
        except AttributeError:
            print("[LK] LKIF_GetCalcData was not found in LkIF.dll.")
            print("[LK] Check the DLL version and the LK-G3000 interface manual.")
            return

        get_calc_data.argtypes = [ctypes.POINTER(LkFloatValue), ctypes.POINTER(LkFloatValue)]
        get_calc_data.restype = ctypes.c_bool

        # Some LkIF.dll versions expose initialization/finalization functions.
        # They are called only if present so the script remains usable with
        # minimal DLL variants.
        for name in ("LKIF_Initialize", "LKIF_OpenDevice", "LKIF_Open"):
            func = getattr(self.dll, name, None)
            if func is None:
                continue
            try:
                func.restype = ctypes.c_bool
                ok = bool(func())
                print(f"[LK] {name}() -> {ok}")
            except Exception as exc:
                print(f"[LK] {name}() failed: {exc}")
            break

        self.available = True
        print(f"[LK] Loaded: {self.dll_path}")

    def read(self) -> LkReading:
        if not self.available or self.dll is None:
            return LkReading(out1_status="not_available", out2_status="not_available")

        out1 = LkFloatValue()
        out2 = LkFloatValue()

        try:
            ok = bool(self.dll.LKIF_GetCalcData(byref(out1), byref(out2)))
        except Exception as exc:
            print(f"[LK] LKIF_GetCalcData failed: {exc}")
            return LkReading(out1_status="read_error", out2_status="read_error")

        if not ok:
            print("[LK] LKIF_GetCalcData returned False.")
            return LkReading(out1_status="dll_false", out2_status="dll_false")

        out1_status = LK_STATUS_NAMES.get(out1.FloatResult, f"status_{out1.FloatResult}")
        out2_status = LK_STATUS_NAMES.get(out2.FloatResult, f"status_{out2.FloatResult}")
        return LkReading(
            out1_mm=float(out1.Value) if out1.FloatResult == 0 else None,
            out2_mm=float(out2.Value) if out2.FloatResult == 0 else None,
            out1_status=out1_status,
            out2_status=out2_status,
        )

    def close(self) -> None:
        if self.dll is None:
            return

        for name in ("LKIF_CloseDevice", "LKIF_Close", "LKIF_Finalize"):
            func = getattr(self.dll, name, None)
            if func is None:
                continue
            try:
                func.restype = ctypes.c_bool
                ok = bool(func())
                print(f"[LK] {name}() -> {ok}")
            except Exception as exc:
                print(f"[LK] {name}() failed: {exc}")


class Dl50Hi:
    def __init__(
        self,
        port: str,
        baudrate: int,
        bytesize: int,
        parity: str,
        stopbits: float,
        output_mode: str,
        timeout_s: float,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.output_mode = output_mode
        self.timeout_s = timeout_s
        self.ser = None
        self._continuous_buffer = b""

    def open(self) -> None:
        try:
            import serial
        except ImportError:
            print("[DL50] pyserial is not installed. Install with: pip install pyserial")
            return

        try:
            self.ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize={
                    7: serial.SEVENBITS,
                    8: serial.EIGHTBITS,
                }[self.bytesize],
                parity={
                    "N": serial.PARITY_NONE,
                    "E": serial.PARITY_EVEN,
                    "O": serial.PARITY_ODD,
                }[self.parity],
                stopbits={
                    1: serial.STOPBITS_ONE,
                    1.5: serial.STOPBITS_ONE_POINT_FIVE,
                    2: serial.STOPBITS_TWO,
                }[self.stopbits],
                timeout=self.timeout_s,
                write_timeout=self.timeout_s,
            )
            print(
                f"[DL50] Opened {self.port} at {self.baudrate} bps, "
                f"{self.bytesize}{self.parity}{self.stopbits:g}, "
                f"mode={self.output_mode}."
            )
        except Exception as exc:
            print(f"[DL50] Failed to open {self.port}: {exc}")
            self.ser = None

    def flush_input(self) -> None:
        if self.ser is None:
            return
        try:
            self.ser.reset_input_buffer()
            self._continuous_buffer = b""
        except Exception as exc:
            print(f"[DL50] Failed to flush input buffer: {exc}")

    def read_fresh(self, settle_s: float = 0.25, samples: int = 3) -> Dl50Reading:
        """Discard queued serial data, then return a recent stable reading.

        DL50 continuous output can leave old lines in the serial buffer. For
        start/end capture and motion decisions, using an old line can make the
        mechanism push against the start point. This method flushes queued data,
        waits briefly for new output, and returns the median of valid samples.
        """
        if self.ser is None:
            return Dl50Reading(raw="not_available")

        self.flush_input()
        time.sleep(max(0.0, settle_s))
        readings: list[Dl50Reading] = []
        deadline = time.perf_counter() + max(self.timeout_s * max(samples, 1) * 4.0, 0.5)
        while len(readings) < samples and time.perf_counter() < deadline:
            reading = self.read()
            if reading.mm is not None:
                readings.append(reading)

        if not readings:
            return Dl50Reading(raw="")

        readings.sort(key=lambda r: float(r.mm))
        return readings[len(readings) // 2]

    def read(self) -> Dl50Reading:
        if self.ser is None:
            return Dl50Reading(raw="not_available")

        try:
            if self.output_mode == "request":
                self.ser.reset_input_buffer()
                self.ser.write(DL50_REQUEST)
                self.ser.flush()
                raw_bytes = self.ser.read_until(b"\x03", size=DL50_MAX_RESPONSE_BYTES)
                if raw_bytes and not raw_bytes.endswith(b"\x03"):
                    more_bytes = self.ser.read_until(b"\n", size=DL50_MAX_RESPONSE_BYTES)
                    raw_bytes += more_bytes
            else:
                raw_bytes = self._read_latest_continuous_line()
        except Exception as exc:
            print(f"[DL50] Serial read/write failed: {exc}")
            return Dl50Reading(raw="serial_error")

        if not raw_bytes:
            print("[DL50] No response.")
            return Dl50Reading(raw="")

        raw_text = raw_bytes.decode("ascii", errors="replace")
        is_stx_etx = raw_bytes.startswith(b"\x02") and raw_bytes.endswith(b"\x03")
        is_crlf_line = raw_bytes.endswith(b"\n") and bool(
            re.search(rb"[+-]?\d{7}\r?\n$", raw_bytes)
        )
        if not (is_stx_etx or is_crlf_line):
            print(f"[DL50] Unexpected frame bytes: {bytes_to_hex(raw_bytes)}")

        try:
            mm = parse_dl50_mm(raw_text)
            return Dl50Reading(mm=mm, raw=raw_text)
        except ValueError as exc:
            print(f"[DL50] Failed to parse value: {exc}; raw_hex={bytes_to_hex(raw_bytes)}")
            return Dl50Reading(raw=compact_raw(raw_bytes))

    def _read_latest_continuous_line(self) -> bytes:
        if self.ser is None:
            return b""

        deadline = time.perf_counter() + self.timeout_s
        complete_lines: list[bytes] = []

        while time.perf_counter() < deadline:
            waiting = self.ser.in_waiting
            if waiting:
                self._continuous_buffer += self.ser.read(
                    min(waiting, DL50_MAX_RESPONSE_BYTES)
                )
            else:
                self._continuous_buffer += self.ser.read(1)

            while b"\n" in self._continuous_buffer:
                line, self._continuous_buffer = self._continuous_buffer.split(b"\n", 1)
                line += b"\n"
                if re.fullmatch(rb"[+-]?\d{7}\r?\n", line):
                    complete_lines.append(line)

            if complete_lines:
                while self.ser.in_waiting:
                    self._continuous_buffer += self.ser.read(
                        min(self.ser.in_waiting, DL50_MAX_RESPONSE_BYTES)
                    )
                    while b"\n" in self._continuous_buffer:
                        line, self._continuous_buffer = self._continuous_buffer.split(b"\n", 1)
                        line += b"\n"
                        if re.fullmatch(rb"[+-]?\d{7}\r?\n", line):
                            complete_lines.append(line)
                return complete_lines[-1]

        return b""

    def close(self) -> None:
        if self.ser is not None:
            self.ser.close()
            print(f"[DL50] Closed {self.port}.")


def parse_dl50_mm(raw_text: str) -> float:
    cleaned = raw_text.strip("\x02\x03\r\n ")
    match = re.search(r"8107([+-]\d+)", cleaned)
    if not match:
        match = re.search(r"0302([+-]\d+)", cleaned)
    if not match:
        match = re.search(r"([+-]?\d{7})", cleaned)
    if not match:
        raise ValueError(
            "response does not match '<STX>8107+xxxxxxx<ETX>', "
            "'<STX>0302+xxxxxxx<ETX>', or '+xxxxxxx<CR><LF>': "
            f"{cleaned!r}"
        )
    return int(match.group(1)) / 10.0


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def compact_raw(data: bytes) -> str:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return "hex:" + bytes_to_hex(data)

    if all((32 <= ord(ch) <= 126) or ch in "\x02\x03\r\n\t" for ch in text):
        return text
    return "hex:" + bytes_to_hex(data)


def format_float(value: Optional[float]) -> str:
    return "" if value is None else f"{value:.6f}"


def make_row(start_t: float, lk: LkReading, dl50: Dl50Reading) -> dict[str, str]:
    now = datetime.now()
    return {
        "pc_time": now.isoformat(timespec="milliseconds"),
        "elapsed_s": f"{time.perf_counter() - start_t:.6f}",
        "lk_out1_mm": format_float(lk.out1_mm),
        "lk_out2_mm": format_float(lk.out2_mm),
        "lk_out1_status": lk.out1_status,
        "lk_out2_status": lk.out2_status,
        "dl50_hi_mm": format_float(dl50.mm),
        "dl50_raw": dl50.raw,
    }


def list_serial_ports() -> None:
    try:
        from serial.tools import list_ports
    except ImportError:
        print("[PORT] pyserial is not installed. Install with: pip install pyserial")
        return

    ports = list(list_ports.comports())
    if not ports:
        print("[PORT] No serial ports found.")
        return

    print("[PORT] Available serial ports:")
    for port in ports:
        print(f"  {port.device}: {port.description}")


def run_logger(
    mode: str,
    csv_path: Path,
    sample_period_s: float,
    dl50_port: str,
    dl50_baudrate: int,
    dl50_bytesize: int,
    dl50_parity: str,
    dl50_stopbits: float,
    dl50_output_mode: str,
    count: Optional[int],
) -> None:
    script_dir = Path(__file__).resolve().parent
    lk = LkG3000(script_dir / LK_DLL_NAME)
    dl50 = Dl50Hi(
        dl50_port,
        dl50_baudrate,
        dl50_bytesize,
        dl50_parity,
        dl50_stopbits,
        dl50_output_mode,
        DL50_TIMEOUT_S,
    )

    use_lk = mode in ("lk", "both")
    use_dl50 = mode in ("dl50", "both")

    if use_lk:
        lk.open()
    if use_dl50:
        dl50.open()

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[CSV] Writing: {csv_path.resolve()}")
    print("[RUN] Press Ctrl+C to stop.")

    start_t = time.perf_counter()
    next_t = start_t

    try:
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()

            sample_count = 0
            while True:
                loop_t = time.perf_counter()

                # These calls are made back-to-back so the timestamps are as close
                # as possible in this minimal synchronous version.
                lk_reading = lk.read() if use_lk else LkReading()
                dl50_reading = dl50.read() if use_dl50 else Dl50Reading()

                row = make_row(start_t, lk_reading, dl50_reading)
                writer.writerow(row)
                f.flush()

                print(
                    f"[{row['pc_time']}] "
                    f"LK OUT1={row['lk_out1_mm']} ({row['lk_out1_status']}), "
                    f"OUT2={row['lk_out2_mm']} ({row['lk_out2_status']}), "
                    f"DL50={row['dl50_hi_mm']} raw={row['dl50_raw']!r}"
                )

                sample_count += 1
                if count is not None and sample_count >= count:
                    print(f"[RUN] Reached requested sample count: {count}")
                    break

                next_t += sample_period_s
                sleep_s = next_t - time.perf_counter()
                if sleep_s > 0:
                    time.sleep(sleep_s)
                else:
                    next_t = loop_t
    except KeyboardInterrupt:
        print("\n[RUN] Ctrl+C received. Stopping safely.")
    finally:
        dl50.close()
        lk.close()
        print("[RUN] Finished.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Log KEYENCE LK-G3000 and SICK DL50 Hi values to CSV."
    )
    parser.add_argument(
        "--mode",
        choices=["dl50", "lk", "both"],
        default="both",
        help="Test mode: DL50 only, LK only, or both sensors.",
    )
    parser.add_argument(
        "--port",
        default=DL50_PORT,
        help=f"DL50 COM port. Default: {DL50_PORT}",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=DL50_BAUDRATE,
        help=f"DL50 baudrate. Default: {DL50_BAUDRATE}",
    )
    parser.add_argument(
        "--bytesize",
        type=int,
        choices=[7, 8],
        default=DL50_BYTESIZE,
        help=f"DL50 data bits. Default: {DL50_BYTESIZE}",
    )
    parser.add_argument(
        "--parity",
        choices=["N", "E", "O"],
        default=DL50_PARITY,
        help=f"DL50 parity: N, E, or O. Default: {DL50_PARITY}",
    )
    parser.add_argument(
        "--stopbits",
        type=float,
        choices=[1, 1.5, 2],
        default=DL50_STOPBITS,
        help=f"DL50 stop bits. Default: {DL50_STOPBITS}",
    )
    parser.add_argument(
        "--dl50-output",
        choices=["request", "continuous"],
        default="request",
        help="DL50 output mode. Use continuous when the sensor streams '+xxxxxxx\\r\\n'.",
    )
    parser.add_argument(
        "--period",
        type=float,
        default=SAMPLE_PERIOD_S,
        help=f"Sample period in seconds. Default: {SAMPLE_PERIOD_S}",
    )
    parser.add_argument(
        "--csv",
        default=CSV_PATH,
        help=f"Output CSV path. Default: {CSV_PATH}",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Stop after this many samples. Default: run until Ctrl+C.",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="List serial ports and exit.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.list_ports:
        list_serial_ports()
        return 0

    if args.period <= 0:
        print("[RUN] --period must be greater than zero.")
        return 2
    if args.count is not None and args.count <= 0:
        print("[RUN] --count must be greater than zero.")
        return 2

    run_logger(
        mode=args.mode,
        csv_path=Path(args.csv),
        sample_period_s=args.period,
        dl50_port=args.port,
        dl50_baudrate=args.baud,
        dl50_bytesize=args.bytesize,
        dl50_parity=args.parity,
        dl50_stopbits=args.stopbits,
        dl50_output_mode=args.dl50_output,
        count=args.count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
