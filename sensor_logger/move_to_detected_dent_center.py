"""
Move the measurement head to the detected dent center.

Workflow:
    1. Manually set the mechanism to the scan start point.
    2. Press Enter. The current DL50 value is stored as the start.
    3. Laser angle servo moves to the detected center angle.
    4. Wheel servo moves until DL50 reaches start + target distance.
    5. Wheel stops automatically.

Close ICS Manager before running this script.
Press any key during motion to stop safely.
"""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path
from typing import Optional

from auto_angle_wheel_scan_logger import (
    STOP_POSITION,
    angle_to_position,
    bytes_to_hex,
    drain_dl50,
    encode_ics_position_command,
    key_pressed,
    open_servo_serial,
    read_latest_valid,
)
from sensor_logger import Dl50Hi, Dl50Reading


DEFAULT_DL50_PORT = "COM10"
DEFAULT_SERVO_PORT = "COM8"
DEFAULT_LASER_ID = 5
DEFAULT_WHEEL_ID = 4
DEFAULT_TARGET_DELTA_MM = 417.0
DEFAULT_TARGET_ANGLE_DEG = 5.0


def parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_detected_center(path: Path) -> tuple[float, float]:
    """Return target_delta_mm_from_original_start, target_angle_deg."""
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"Empty detected region CSV: {path}")

    center_axis_s = None
    center_angle = None
    near_axis_values: list[float] = []

    for row in rows:
        if center_axis_s is None:
            center_axis_s = parse_float(row.get("dent_center_axis_s_mm"))
        if center_angle is None:
            center_angle = parse_float(row.get("dent_center_angle_deg"))

    if center_axis_s is None or center_angle is None:
        raise SystemExit("Could not read dent_center_axis_s_mm / dent_center_angle_deg.")

    for row in rows:
        axis_s = parse_float(row.get("axis_s_mm"))
        angle = parse_float(row.get("angle_deg"))
        original_axis = parse_float(row.get("axis_mm")) or parse_float(row.get("x_mm"))
        if axis_s is None or angle is None or original_axis is None:
            continue
        if abs(axis_s - center_axis_s) <= 5.0 and abs(angle - center_angle) <= 2.6:
            near_axis_values.append(original_axis)

    if not near_axis_values:
        raise SystemExit("Could not convert detected center to original DL50-axis distance.")

    near_axis_values.sort()
    mid = len(near_axis_values) // 2
    if len(near_axis_values) % 2:
        target_delta = near_axis_values[mid]
    else:
        target_delta = 0.5 * (near_axis_values[mid - 1] + near_axis_values[mid])
    return target_delta, center_angle


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


def move_to_target(args: argparse.Namespace) -> None:
    if args.detected_region:
        target_delta_mm, target_angle_deg = load_detected_center(Path(args.detected_region))
        print(f"[TARGET] 検出CSVから目標を読み込みました: angle={target_angle_deg:.3f} deg, delta={target_delta_mm:.3f} mm")
    else:
        target_delta_mm = args.target_delta_mm
        target_angle_deg = args.target_angle_deg
        print(f"[TARGET] 手動指定の目標を使います: angle={target_angle_deg:.3f} deg, delta={target_delta_mm:.3f} mm")

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
        print("[準備] 測定ヘッドを手で『始点』に合わせてください。")
        print("[準備] 合わせたら Enter を押してください。現在のDL50値を始点として記録します。")
        print("============================================================")
        input("> ")

        latest = dl50.read_fresh(settle_s=0.35, samples=3) if hasattr(dl50, "read_fresh") else drain_dl50(dl50, seconds=0.8)
        if latest.mm is None:
            raise RuntimeError("DL50の始点値を取得できませんでした。COMポートやDL50表示を確認してください。")

        start_mm = latest.mm
        target_abs_mm = start_mm + target_delta_mm
        print(f"[START] DL50始点 = {start_mm:.3f} mm")
        print(f"[TARGET] 目標DL50 = {target_abs_mm:.3f} mm  (始点 + {target_delta_mm:.3f} mm)")

        laser_pos = angle_to_position(
            target_angle_deg,
            args.min_deg,
            args.max_deg,
            args.min_pos,
            args.max_pos,
        )
        print(f"[LASER] 角度 {target_angle_deg:.3f} deg -> position {laser_pos}")
        send_position(ser, args.laser_id, laser_pos, "LASER")
        time.sleep(args.angle_settle_s)

        print()
        print("============================================================")
        print("[移動] 車輪サーボで窪み中心まで移動します。")
        print("[移動] 途中停止したい場合はPowerShellで何かキーを押してください。")
        print("============================================================")

        # Decide direction from target relative to current DL50.
        latest = dl50.read_fresh(settle_s=0.15, samples=2) if hasattr(dl50, "read_fresh") else read_latest_valid(dl50, latest)
        if latest.mm is None:
            raise RuntimeError("DL50値を取得できませんでした。")
        remaining = target_abs_mm - latest.mm
        if abs(remaining) <= args.tolerance_mm:
            print("[DONE] すでに目標位置付近です。")
            stop_wheel(ser, args.wheel_id)
            return

        wheel_cmd = args.wheel_forward_speed if remaining > 0 else args.wheel_reverse_speed
        print(f"[WHEEL] command={wheel_cmd}  remaining={remaining:.3f} mm")
        send_position(ser, args.wheel_id, wheel_cmd, "WHEEL")

        stable_count = 0
        last_mm = latest.mm
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
                        print("[DONE] 目標位置に到達しました。")
                        break
                else:
                    stable_count = 0

                # Stop if it clearly overshot past the target.
                if remaining > 0 and error < -args.overshoot_stop_mm:
                    print()
                    print("[STOP] 目標を通り過ぎたため停止します。")
                    break
                if remaining < 0 and error > args.overshoot_stop_mm:
                    print()
                    print("[STOP] 目標を通り過ぎたため停止します。")
                    break

                if abs(latest.mm - last_mm) < args.stall_mm:
                    # Do not stop immediately; DL50 continuous output can repeat.
                    pass
                last_mm = latest.mm

                if key_pressed():
                    print()
                    print("[STOP] キー入力を検出したため停止します。")
                    break
                time.sleep(args.period)
            else:
                print()
                print("[STOP] 制限時間に達したため停止します。")
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
    parser = argparse.ArgumentParser(description="Move to detected dent center using DL50 and wheel servo.")
    parser.add_argument("--dl50-port", default=DEFAULT_DL50_PORT)
    parser.add_argument("--dl50-baud", type=int, default=115200)
    parser.add_argument("--dl50-bytesize", type=int, default=7)
    parser.add_argument("--dl50-parity", default="E")
    parser.add_argument("--dl50-stopbits", type=int, default=1)
    parser.add_argument("--servo-port", default=DEFAULT_SERVO_PORT)
    parser.add_argument("--servo-baud", type=int, default=115200)
    parser.add_argument("--laser-id", type=int, default=DEFAULT_LASER_ID)
    parser.add_argument("--wheel-id", type=int, default=DEFAULT_WHEEL_ID)
    parser.add_argument("--detected-region", help="auto_scan_..._detected_dent_region.csv")
    parser.add_argument("--target-delta-mm", type=float, default=DEFAULT_TARGET_DELTA_MM)
    parser.add_argument("--target-angle-deg", type=float, default=DEFAULT_TARGET_ANGLE_DEG)
    parser.add_argument("--wheel-forward-speed", type=int, default=7350)
    parser.add_argument("--wheel-reverse-speed", type=int, default=7650)
    parser.add_argument("--tolerance-mm", type=float, default=1.0)
    parser.add_argument("--overshoot-stop-mm", type=float, default=1.5)
    parser.add_argument("--stable-samples", type=int, default=2)
    parser.add_argument("--period", type=float, default=0.03)
    parser.add_argument("--max-seconds", type=float, default=90.0)
    parser.add_argument("--angle-settle-s", type=float, default=0.4)
    parser.add_argument("--stall-mm", type=float, default=0.05)
    parser.add_argument("--min-deg", type=float, default=-135.0)
    parser.add_argument("--max-deg", type=float, default=135.0)
    parser.add_argument("--min-pos", type=int, default=3500)
    parser.add_argument("--max-pos", type=int, default=11500)
    args = parser.parse_args()

    move_to_target(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
