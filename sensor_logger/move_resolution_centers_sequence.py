#!/usr/bin/env python3
"""
Sequentially move to centers estimated under multiple resolution conditions.

The start point is recorded only once. Every condition then uses the same
physical start origin:

    target DL50 = recorded start DL50 + center_x_zero_mm

Default order follows the rows in the resolution results CSV, e.g.
a5_x1 -> a5_x2 -> ... -> a15_x10.
"""

from __future__ import annotations

import argparse
import csv
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


def parse_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_results(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def condition_key(row: dict[str, str]) -> str:
    return str(row.get("condition", "")).strip()


def select_rows(rows: list[dict[str, str]], conditions: str | None) -> list[dict[str, str]]:
    if not conditions:
        return rows

    by_condition = {condition_key(row): row for row in rows}
    selected = []
    missing = []
    for condition in [part.strip() for part in conditions.split(",") if part.strip()]:
        row = by_condition.get(condition)
        if row is None:
            missing.append(condition)
        else:
            selected.append(row)

    if missing:
        available = ", ".join(condition_key(row) for row in rows)
        raise SystemExit(f"Unknown conditions: {', '.join(missing)}\nAvailable: {available}")
    return selected


def get_target(row: dict[str, str]) -> tuple[float, float]:
    target_delta = parse_float(row.get("center_x_zero_mm"))
    target_angle = parse_float(row.get("center_angle_deg"))
    if target_delta is None or target_angle is None:
        raise SystemExit(f"Invalid target row: {condition_key(row)}")
    return target_delta, target_angle


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


def print_condition_table(rows: list[dict[str, str]]) -> None:
    print("condition, angle_step_deg, x_step_mm, rows, center_x_zero_mm, center_angle_deg, error_surface_mm")
    for row in rows:
        print(
            f"{condition_key(row)}, {row.get('angle_step_deg')}, {row.get('x_step_mm')}, "
            f"{row.get('raw_rows')}, {row.get('center_x_zero_mm')}, {row.get('center_angle_deg')}, "
            f"{row.get('error_surface_mm')}"
        )


def print_target(row: dict[str, str], index: int, total: int, target_delta: float, target_angle: float) -> None:
    print()
    print("============================================================")
    print(f"[TARGET {index}/{total}] {condition_key(row)}")
    print(f"angle step / x step : {row.get('angle_step_deg')} deg / {row.get('x_step_mm')} mm")
    print(f"source rows         : {row.get('raw_rows')}")
    print(f"target from start   : {target_delta:.3f} mm")
    print(f"target angle        : {target_angle:.3f} deg")
    print(f"error vs a5_x1      : {row.get('error_surface_mm')} mm")
    print(f"combined score      : {row.get('combined_score')}")
    print("============================================================")


def move_one_target(
    args: argparse.Namespace,
    ser,
    dl50: Dl50Hi,
    start_mm: float,
    target_delta_mm: float,
    target_angle_deg: float,
    latest: Dl50Reading,
) -> Dl50Reading:
    target_abs_mm = start_mm + target_delta_mm
    laser_pos = angle_to_position(
        target_angle_deg,
        args.min_deg,
        args.max_deg,
        args.min_pos,
        args.max_pos,
    )

    print(f"[LASER] angle={target_angle_deg:.3f} deg -> position={laser_pos}")
    send_position(ser, args.laser_id, laser_pos, "LASER")
    time.sleep(args.angle_settle_s)

    latest = dl50.read_fresh(settle_s=0.15, samples=2) if hasattr(dl50, "read_fresh") else read_latest_valid(dl50, latest)
    if latest.mm is None:
        raise RuntimeError("Could not read DL50 before motion.")

    remaining = target_abs_mm - latest.mm
    print(f"[TARGET] DL50 target={target_abs_mm:.3f} mm, current={latest.mm:.3f} mm, remaining={remaining:.3f} mm")
    if abs(remaining) <= args.tolerance_mm:
        print("[DONE] Already near this target.")
        stop_wheel(ser, args.wheel_id)
        return latest

    wheel_cmd = args.wheel_forward_speed if remaining > 0 else args.wheel_reverse_speed
    send_position(ser, args.wheel_id, wheel_cmd, "WHEEL")
    print(f"[WHEEL] command={wheel_cmd}. Press any key in PowerShell to stop.")

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
                    print("[DONE] Reached this target.")
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
                print("[STOP] Key input detected; stopping this move.")
                break

            time.sleep(args.period)
        else:
            print()
            print("[STOP] Time limit reached.")
    finally:
        stop_wheel(ser, args.wheel_id)
        if latest.mm is not None:
            print(f"[FINAL] DL50={latest.mm:.3f} mm, target={target_abs_mm:.3f} mm, error={target_abs_mm - latest.mm:.3f} mm")

    return latest


def run_sequence(args: argparse.Namespace, rows: list[dict[str, str]]) -> None:
    if not rows:
        raise SystemExit("No target rows selected.")

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
        print("[START SETUP]")
        print("Move the device to the SAME physical scan start point.")
        print("This start value will be reused for all resolution conditions.")
        print("Press Enter after alignment.")
        print("============================================================")
        input("> ")

        latest = dl50.read_fresh(settle_s=0.35, samples=3) if hasattr(dl50, "read_fresh") else read_latest_valid(dl50, latest)
        if latest.mm is None:
            raise RuntimeError("Could not read DL50 start value.")
        start_mm = latest.mm
        print(f"[START] DL50 start = {start_mm:.3f} mm")

        total = len(rows)
        for index, row in enumerate(rows, start=1):
            target_delta, target_angle = get_target(row)
            print_target(row, index, total, target_delta, target_angle)
            latest = move_one_target(args, ser, dl50, start_mm, target_delta, target_angle, latest)

            if index >= total:
                break

            print()
            print("============================================================")
            print(f"[NEXT] Current condition: {condition_key(row)}")
            print("Check the laser position or take a note.")
            print("Press Enter to move to the next condition, or type q then Enter to stop.")
            print("============================================================")
            answer = input("> ").strip().lower()
            if answer == "q":
                print("[STOP] Sequence stopped by user.")
                break

        print("[DONE] Sequence finished.")

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
    parser = argparse.ArgumentParser(description="Move through all resolution-study dent centers in sequence.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--conditions", help="Comma-separated condition list. Default: all rows in CSV order.")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Actually open COM ports and move the mechanism.")
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
        cwd_path = Path.cwd() / args.results
        script_path = script_dir / args.results
        args.results = cwd_path if cwd_path.exists() else script_path

    rows = select_rows(read_results(args.results), args.conditions)
    if args.list:
        print_condition_table(rows)
        return 0

    print_condition_table(rows)
    if not args.execute:
        print()
        print("[DRY RUN] COM ports were not opened. Add --execute to move through the sequence.")
        return 0

    run_sequence(args, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
