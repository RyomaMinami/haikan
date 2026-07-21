"""
Record LK-G values every time DL50 distance increases by a fixed step.

Workflow:
    1. Start this script.
    2. Press Enter when the current DL50 value should be used as the baseline.
    3. The script records LK-G OUT1/OUT2 each time DL50 has increased by
       STEP_MM from that baseline.
    4. Press q or Ctrl+C to stop.

Default DL50 settings match the current DL50-N222S01 setup:
    115200 bps, 7E1, continuous output, '+xxxxxxx\\r\\n'
"""

from __future__ import annotations

import argparse
import csv
import math
import msvcrt
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from sensor_logger import Dl50Hi, Dl50Reading, LkG3000, LkReading, format_float


DEFAULT_PORT = "COM10"
DEFAULT_BAUD = 115200
DEFAULT_BYTESIZE = 7
DEFAULT_PARITY = "E"
DEFAULT_STOPBITS = 1
DEFAULT_DL50_OUTPUT = "continuous"
DEFAULT_PERIOD_S = 0.02
DEFAULT_STEP_MM = 5.0
DEFAULT_CSV = "step_trigger_log.csv"


CSV_COLUMNS = [
    "pc_time",
    "elapsed_s",
    "trigger_index",
    "target_delta_mm",
    "dl50_initial_mm",
    "dl50_hi_mm",
    "dl50_delta_mm",
    "dl50_raw",
    "lk_out1_mm",
    "lk_out2_mm",
    "lk_out1_status",
    "lk_out2_status",
]


def read_key() -> Optional[str]:
    if not msvcrt.kbhit():
        return None

    ch = msvcrt.getwch()
    if ch == "\r":
        return "enter"
    return ch


def make_row(
    start_t: float,
    trigger_index: int,
    target_delta_mm: float,
    initial_mm: float,
    dl50: Dl50Reading,
    lk: LkReading,
) -> dict[str, str]:
    dl50_delta = None if dl50.mm is None else dl50.mm - initial_mm
    return {
        "pc_time": datetime.now().isoformat(timespec="milliseconds"),
        "elapsed_s": f"{time.perf_counter() - start_t:.6f}",
        "trigger_index": str(trigger_index),
        "target_delta_mm": f"{target_delta_mm:.3f}",
        "dl50_initial_mm": format_float(initial_mm),
        "dl50_hi_mm": format_float(dl50.mm),
        "dl50_delta_mm": format_float(dl50_delta),
        "dl50_raw": dl50.raw,
        "lk_out1_mm": format_float(lk.out1_mm),
        "lk_out2_mm": format_float(lk.out2_mm),
        "lk_out1_status": lk.out1_status,
        "lk_out2_status": lk.out2_status,
    }


def open_csv_for_write(csv_path: Path):
    try:
        return csv_path.open("w", newline="", encoding="utf-8-sig"), csv_path
    except PermissionError:
        stamped_path = csv_path.with_name(
            f"{csv_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{csv_path.suffix}"
        )
        print(f"[CSV] Cannot write {csv_path}; using {stamped_path} instead.")
        return stamped_path.open("w", newline="", encoding="utf-8-sig"), stamped_path


def run(
    port: str,
    baud: int,
    bytesize: int,
    parity: str,
    stopbits: float,
    dl50_output: str,
    period_s: float,
    step_mm: float,
    csv_path: Path,
    count: Optional[int],
) -> None:
    script_dir = Path(__file__).resolve().parent
    lk = LkG3000(script_dir / "LkIF.dll")
    dl50 = Dl50Hi(port, baud, bytesize, parity, stopbits, dl50_output, timeout_s=0.2)

    lk.open()
    dl50.open()

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    print("[RUN] Press Enter to set DL50 initial value. Press q or Ctrl+C to stop.")

    start_t = time.perf_counter()
    initial_mm: Optional[float] = None
    next_trigger_index = 1
    last_dl50 = Dl50Reading()
    recorded_dl50_keys: set[int] = set()

    try:
        f, actual_csv_path = open_csv_for_write(csv_path)
        print(f"[CSV] Writing: {actual_csv_path.resolve()}")
        with f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()

            while True:
                key = read_key()
                if key in ("q", "Q"):
                    print("[RUN] q received. Stopping.")
                    break

                dl50_reading = dl50.read()
                if dl50_reading.mm is not None:
                    last_dl50 = dl50_reading

                if key == "enter":
                    if last_dl50.mm is None:
                        print("[BASE] Cannot set initial value yet: no valid DL50 value.")
                    else:
                        initial_mm = last_dl50.mm
                        next_trigger_index = 1
                        recorded_dl50_keys = set()
                        print(f"[BASE] DL50 initial value set: {initial_mm:.6f} mm")

                if initial_mm is not None and last_dl50.mm is not None:
                    delta_mm = last_dl50.mm - initial_mm
                    reached_index = math.floor(delta_mm / step_mm)
                    dl50_key = round(last_dl50.mm * 1000)
                    if reached_index >= next_trigger_index and dl50_key not in recorded_dl50_keys:
                        trigger_index = reached_index
                        target_delta_mm = trigger_index * step_mm
                        lk_reading = lk.read()
                        row = make_row(
                            start_t,
                            trigger_index,
                            target_delta_mm,
                            initial_mm,
                            last_dl50,
                            lk_reading,
                        )
                        writer.writerow(row)
                        f.flush()
                        recorded_dl50_keys.add(dl50_key)

                        print(
                            f"[TRIG {trigger_index}] "
                            f"delta={row['dl50_delta_mm']} mm "
                            f"DL50={row['dl50_hi_mm']} "
                            f"LK OUT1={row['lk_out1_mm']} ({row['lk_out1_status']}), "
                            f"OUT2={row['lk_out2_mm']} ({row['lk_out2_status']})"
                        )

                        next_trigger_index = trigger_index + 1
                        if count is not None and len(recorded_dl50_keys) >= count:
                            print(f"[RUN] Reached requested trigger count: {count}")
                            return

                time.sleep(period_s)
    except KeyboardInterrupt:
        print("\n[RUN] Ctrl+C received. Stopping safely.")
    finally:
        dl50.close()
        lk.close()
        print("[RUN] Finished.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record LK-G values every DL50 distance step after Enter baseline."
    )
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--bytesize", type=int, choices=[7, 8], default=DEFAULT_BYTESIZE)
    parser.add_argument("--parity", choices=["N", "E", "O"], default=DEFAULT_PARITY)
    parser.add_argument("--stopbits", type=float, choices=[1, 1.5, 2], default=DEFAULT_STOPBITS)
    parser.add_argument(
        "--dl50-output",
        choices=["request", "continuous"],
        default=DEFAULT_DL50_OUTPUT,
    )
    parser.add_argument("--period", type=float, default=DEFAULT_PERIOD_S)
    parser.add_argument("--step-mm", type=float, default=DEFAULT_STEP_MM)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Stop after this many trigger records. Default: run until q/Ctrl+C.",
    )
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.period <= 0:
        print("[RUN] --period must be greater than zero.")
        return 2
    if args.step_mm <= 0:
        print("[RUN] --step-mm must be greater than zero.")
        return 2
    if args.count is not None and args.count <= 0:
        print("[RUN] --count must be greater than zero.")
        return 2

    run(
        port=args.port,
        baud=args.baud,
        bytesize=args.bytesize,
        parity=args.parity,
        stopbits=args.stopbits,
        dl50_output=args.dl50_output,
        period_s=args.period,
        step_mm=args.step_mm,
        csv_path=Path(args.csv),
        count=args.count,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
