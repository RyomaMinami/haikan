"""
Live display for KEYENCE LK-G85A / LK-G3000 values.

Use this while physically adjusting tilt/alignment.

Run:
    python lk_live_display.py

Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from sensor_logger import LkG3000, format_float


def run(period_s: float, show_delta: bool) -> None:
    script_dir = Path(__file__).resolve().parent
    lk = LkG3000(script_dir / "LkIF.dll")
    lk.open()

    base_out1 = None
    base_out2 = None

    print("[RUN] LK-G live display. Press Ctrl+C to stop.")
    print("[RUN] First valid reading is used as delta baseline.")

    try:
        while True:
            reading = lk.read()

            if base_out1 is None and reading.out1_mm is not None:
                base_out1 = reading.out1_mm
            if base_out2 is None and reading.out2_mm is not None:
                base_out2 = reading.out2_mm

            if show_delta:
                d1 = None if base_out1 is None or reading.out1_mm is None else reading.out1_mm - base_out1
                d2 = None if base_out2 is None or reading.out2_mm is None else reading.out2_mm - base_out2
                line = (
                    f"OUT1={format_float(reading.out1_mm):>12} mm "
                    f"({reading.out1_status:<8}) "
                    f"d1={format_float(d1):>12} mm | "
                    f"OUT2={format_float(reading.out2_mm):>12} mm "
                    f"({reading.out2_status:<8}) "
                    f"d2={format_float(d2):>12} mm"
                )
            else:
                line = (
                    f"OUT1={format_float(reading.out1_mm):>12} mm "
                    f"({reading.out1_status:<8}) | "
                    f"OUT2={format_float(reading.out2_mm):>12} mm "
                    f"({reading.out2_status:<8})"
                )

            print("\r" + line, end="", flush=True)
            time.sleep(period_s)
    except KeyboardInterrupt:
        print("\n[RUN] Ctrl+C received. Stopping.")
    finally:
        lk.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Live display LK-G OUT1/OUT2.")
    parser.add_argument("--period", type=float, default=0.1)
    parser.add_argument("--no-delta", action="store_true")
    args = parser.parse_args()

    if args.period <= 0:
        print("--period must be greater than zero.")
        return 2

    run(args.period, show_delta=not args.no_delta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
