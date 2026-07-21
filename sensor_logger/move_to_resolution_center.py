#!/usr/bin/env python3
"""
Move the device to a dent center estimated from resolution ablation results.

Use this after creating a resolution study CSV with resolution_ablation_study.py.
The target distance is center_x_zero_mm, so the device must be placed at the
same physical scan start point before pressing Enter.
"""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
import time
from pathlib import Path

from auto_angle_wheel_scan_logger import (
    STOP_POSITION,
    angle_to_position,
    bytes_to_hex,
    encode_ics_position_command,
    key_pressed,
    open_servo_serial,
    read_latest_valid,
)
from sensor_logger import Dl50Hi, Dl50Reading


DEFAULT_RESULTS = Path("pipe154_resolution_study_full") / "pipe154_auto_scan_m115_115_resolution_results.csv"
DEFAULT_CONDITION = "a10_x5"


def parse_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_results(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def find_condition(rows: list[dict[str, str]], condition: str) -> dict[str, str]:
    if condition == "best":
        candidates = []
        for row in rows:
            if row.get("condition") == "a5_x1":
                continue
            score = parse_float(row.get("error_surface_mm"))
            if score is None:
                continue
            candidates.append((score, row))
        if not candidates:
            raise SystemExit("No valid non-reference result rows were found.")
        candidates.sort(key=lambda item: item[0])
        return candidates[0][1]

    for row in rows:
        if row.get("condition") == condition:
            return row
    available = ", ".join(row.get("condition", "") for row in rows)
    raise SystemExit(f"Condition not found: {condition}\nAvailable: {available}")


def send_position(ser, servo_id: int, position: int, label: str) -> None:
    command = encode_ics_position_command(servo_id, position)
    ser.reset_input_buffer()
    ser.write(command)
    ser.flush()
    time.sleep(0.015)
    response = ser.read(16)
    print(f"[{label}] id={servo_id} pos={position} TX={bytes_to_hex(command)} RX={bytes_to_hex(response)}")


def stop_wheel(ser, wheel_id: int) -> None:
    send_position(ser, wheel_id, STOP_POSITION, "WHEEL")


def get_target(row: dict[str, str]) -> tuple[float, float]:
    target_delta = parse_float(row.get("center_x_zero_mm"))
    target_angle = parse_float(row.get("center_angle_deg"))
    if target_delta is None or target_angle is None:
        raise SystemExit("Selected row does not contain center_x_zero_mm / center_angle_deg.")
    return target_delta, target_angle


def print_row_summary(row: dict[str, str], target_delta: float, target_angle: float) -> None:
    print("============================================================")
    print("[SELECTED]")
    print(f"condition          : {row.get('condition')}")
    print(f"angle step / x step: {row.get('angle_step_deg')} deg / {row.get('x_step_mm')} mm")
    print(f"rows               : {row.get('raw_rows')}")
    print(f"target delta       : {target_delta:.3f} mm from the physical scan start")
    print(f"target angle       : {target_angle:.3f} deg")
    print(f"error vs a5_x1     : {row.get('error_surface_mm')} mm")
    print(f"combined score     : {row.get('combined_score')}")
    print("============================================================")


def move_to_target(args: argparse.Namespace, target_delta_mm: float, target_angle_deg: float) -> None:
    dl50 = Dl50Hi(
        args.dl50_port,
        args.dl50_baud,
        args.dl50_bytesize,
        args.dl50_parity,
        args.dl50_stopbits,
        "continuous",
        timeout_s=0.2,
    )
    ser = None
    latest = Dl50Reading()

    try:
        ser = open_servo_serial(args.servo_port, args.servo_baud)
        print(f"[SERVO] Opened {args.servo_port} at {args.servo_baud} bps, 8E1")
        dl50.open()
        print(f"[DL50] Opened {args.dl50_port} at {args.dl50_baud} bps, 7E1, continuous")

        print()
        print("============================================================")
        print("[STEP 1] Return the device to the SAME physical scan start point.")
        print("[STEP 1] This fixes the coordinate origin for the downsampled result.")
        print("[STEP 1] Press Enter after alignment.")
        print("============================================================")
        input("> ")

        latest = dl50.read_fresh(settle_s=0.35, samples=3) if hasattr(dl50, "read_fresh") else read_latest_valid(dl50, latest)
        if latest.mm is None:
            raise RuntimeError("Could not read DL50 start value.")

        start_mm = latest.mm
        target_abs_mm = start_mm + target_delta_mm
        print(f"[START] DL50 start = {start_mm:.3f} mm")
        print(f"[TARGET] target DL50 = {target_abs_mm:.3f} mm  (start + {target_delta_mm:.3f} mm)")

        laser_pos = angle_to_position(
            target_angle_deg,
            args.min_deg,
            args.max_deg,
            args.min_pos,
            args.max_pos,
        )
        print(f"[STEP 2] Move laser servo: angle={target_angle_deg:.3f} deg -> position={laser_pos}")
        send_position(ser, args.laser_id, laser_pos, "LASER")
        time.sleep(args.angle_settle_s)

        latest = dl50.read_fresh(settle_s=0.15, samples=2) if hasattr(dl50, "read_fresh") else read_latest_valid(dl50, latest)
        if latest.mm is None:
            raise RuntimeError("Could not read DL50 before wheel motion.")

        remaining = target_abs_mm - latest.mm
        if abs(remaining) <= args.tolerance_mm:
            print("[DONE] Already near target.")
            stop_wheel(ser, args.wheel_id)
            return

        wheel_cmd = args.wheel_forward_speed if remaining > 0 else args.wheel_reverse_speed
        print("============================================================")
        print("[STEP 3] Wheel servo will move to the selected center.")
        print("[STEP 3] Press any key in PowerShell to stop during motion.")
        print("============================================================")
        print(f"[WHEEL] command={wheel_cmd}  remaining={remaining:.3f} mm")
        send_position(ser, args.wheel_id, wheel_cmd, "WHEEL")

        stable_count = 0
        deadline = time.perf_counter() + args.max_seconds

        try:
            while time.perf_counter() < deadline:
                latest = read_latest_valid(dl50, latest)
                if latest.mm is None:
                    time.sleep(args.period)
                    continue

                error = target_abs_mm - latest.mm
                moved_delta = latest.mm - start_mm
                print(
                    f"\r[MOVE] DL50={latest.mm:.3f} mm  "
                    f"delta={moved_delta:.3f}/{target_delta_mm:.3f} mm  "
                    f"error={error:.3f} mm",
                    end="",
                )

                if abs(error) <= args.tolerance_mm:
                    stable_count += 1
                    if stable_count >= args.stable_samples:
                        print()
                        print("[DONE] Reached target center.")
                        break
                else:
                    stable_count = 0

                if remaining > 0 and error < -args.overshoot_stop_mm:
                    print()
                    print("[STOP] Overshot target; stopping.")
                    break
                if remaining < 0 and error > args.overshoot_stop_mm:
                    print()
                    print("[STOP] Overshot target; stopping.")
                    break

                if key_pressed():
                    print()
                    print("[STOP] Key input detected; stopping.")
                    break

                time.sleep(args.period)
            else:
                print()
                print("[STOP] Time limit reached.")
        finally:
            stop_wheel(ser, args.wheel_id)
            if latest.mm is not None:
                print(f"[FINAL] DL50={latest.mm:.3f} mm, target={target_abs_mm:.3f} mm, error={target_abs_mm - latest.mm:.3f} mm")

    finally:
        if ser is not None and ser.is_open:
            try:
                stop_wheel(ser, args.wheel_id)
            except Exception:
                pass
            ser.close()
            print(f"[SERVO] Closed {args.servo_port}")
        dl50.close()
        print(f"[DL50] Closed {args.dl50_port}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Move to a center estimated from resolution study results.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--condition", default=DEFAULT_CONDITION, help="Example: a5_x5, a10_x5, a15_x10, or best")
    parser.add_argument("--list", action="store_true", help="List available conditions and exit")
    parser.add_argument("--execute", action="store_true", help="Actually open COM ports and move the mechanism")
    parser.add_argument("--dl50-port", default="COM10")
    parser.add_argument("--dl50-baud", type=int, default=115200)
    parser.add_argument("--dl50-bytesize", type=int, default=7)
    parser.add_argument("--dl50-parity", default="E")
    parser.add_argument("--dl50-stopbits", type=int, default=1)
    parser.add_argument("--servo-port", default="COM8")
    parser.add_argument("--servo-baud", type=int, default=115200)
    parser.add_argument("--laser-id", type=int, default=5)
    parser.add_argument("--wheel-id", type=int, default=4)
    parser.add_argument("--wheel-forward-speed", type=int, default=7350)
    parser.add_argument("--wheel-reverse-speed", type=int, default=7650)
    parser.add_argument("--tolerance-mm", type=float, default=1.0)
    parser.add_argument("--overshoot-stop-mm", type=float, default=1.5)
    parser.add_argument("--stable-samples", type=int, default=2)
    parser.add_argument("--period", type=float, default=0.03)
    parser.add_argument("--max-seconds", type=float, default=90.0)
    parser.add_argument("--angle-settle-s", type=float, default=0.4)
    parser.add_argument("--min-deg", type=float, default=-135.0)
    parser.add_argument("--max-deg", type=float, default=135.0)
    parser.add_argument("--min-pos", type=int, default=3500)
    parser.add_argument("--max-pos", type=int, default=11500)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    if not args.results.is_absolute():
        args.results = script_dir / args.results

    rows = read_results(args.results)
    if args.list:
        print("condition, angle_step_deg, x_step_mm, rows, center_x_zero_mm, center_angle_deg, error_surface_mm")
        for row in rows:
            print(
                f"{row.get('condition')}, {row.get('angle_step_deg')}, {row.get('x_step_mm')}, "
                f"{row.get('raw_rows')}, {row.get('center_x_zero_mm')}, {row.get('center_angle_deg')}, "
                f"{row.get('error_surface_mm')}"
            )
        return 0

    row = find_condition(rows, args.condition)
    target_delta, target_angle = get_target(row)
    print_row_summary(row, target_delta, target_angle)

    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--results",
        str(args.results),
        "--condition",
        str(args.condition),
        "--dl50-port",
        args.dl50_port,
        "--servo-port",
        args.servo_port,
        "--execute",
    ]
    print("[COMMAND] To move, run:")
    print(" ".join(subprocess.list2cmdline([part]) for part in command))

    if not args.execute:
        print("[DRY RUN] COM ports were not opened. Add --execute to move.")
        return 0

    move_to_target(args, target_delta, target_angle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
