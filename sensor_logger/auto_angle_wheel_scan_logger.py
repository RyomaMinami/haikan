"""
Automatic angle-by-angle pipe scan logger.

Purpose:
    - Laser angle servo ID 5: sweep -90 to +90 deg every 5 deg.
    - Wheel endless-rotation servo ID 4: move along pipe axis automatically.
    - DL50 Hi: monitor axial position.
    - LK-G85A/LK-G3000: record dent signal every 1 mm.

Recommended workflow:
    1. Put the mechanism at the scan start point and press Enter.
    2. Put the mechanism at the scan end point and press Enter.
    3. The script returns to the start point with the wheel servo.
    4. For each angle, the script scans start -> end and records every 1 mm.
    5. The script returns end -> start without recording, then moves to next angle.

Close ICS Manager before running this script.
Press any key during motion to stop the current scan safely.
"""

from __future__ import annotations

import argparse
import csv
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from sensor_logger import Dl50Hi, Dl50Reading, LkG3000, LkReading, format_float


DEFAULT_DL50_PORT = "COM10"
DEFAULT_SERVO_PORT = "COM3"
DEFAULT_LASER_ID = 5
DEFAULT_WHEEL_ID = 4
DEFAULT_CSV = "auto_angle_wheel_scan_1mm.csv"

DEFAULT_MIN_DEG = -135.0
DEFAULT_MAX_DEG = 135.0
DEFAULT_MIN_POS = 3500
DEFAULT_MAX_POS = 11500

STOP_POSITION = 7500
FREE_POSITION = 0


CSV_COLUMNS = [
    "pc_time",
    "elapsed_s",
    "angle_group",
    "angle_deg",
    "pass_index",
    "motion_direction",
    "laser_position",
    "wheel_position",
    "trigger_index",
    "target_delta_mm",
    "scan_start_dl50_mm",
    "scan_end_dl50_mm",
    "dl50_hi_mm",
    "dl50_delta_mm",
    "dl50_progress_mm",
    "dl50_raw",
    "lk_out1_mm",
    "lk_out2_mm",
    "lk_out1_status",
    "lk_out2_status",
    "skipped_targets",
    "sample_note",
]


def angle_to_position(
    angle_deg: float,
    min_deg: float,
    max_deg: float,
    min_pos: int,
    max_pos: int,
) -> int:
    clamped = max(min_deg, min(max_deg, angle_deg))
    ratio = (clamped - min_deg) / (max_deg - min_deg)
    return round(min_pos + ratio * (max_pos - min_pos))


def encode_ics_position_command(servo_id: int, position: int) -> bytes:
    position = max(0, min(11500, int(position)))
    return bytes(
        [
            0x80 | (servo_id & 0x1F),
            (position >> 7) & 0x7F,
            position & 0x7F,
        ]
    )


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def open_servo_serial(port: str, baud: int):
    import serial

    return serial.Serial(
        port=port,
        baudrate=baud,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_EVEN,
        stopbits=serial.STOPBITS_ONE,
        timeout=0.1,
        write_timeout=0.1,
    )


def send_servo_position(ser, servo_id: int, position: int, label: str) -> None:
    command = encode_ics_position_command(servo_id, position)
    ser.reset_input_buffer()
    ser.write(command)
    ser.flush()
    time.sleep(0.012)
    response = ser.read(16)
    print(f"[{label}] id={servo_id} pos={position} TX={bytes_to_hex(command)} RX={bytes_to_hex(response)}")


def stop_wheel(ser, wheel_id: int) -> None:
    send_servo_position(ser, wheel_id, STOP_POSITION, "WHEEL")


def free_wheel(ser, wheel_id: int) -> None:
    send_servo_position(ser, wheel_id, FREE_POSITION, "WHEEL")


def key_pressed() -> bool:
    try:
        import msvcrt

        if msvcrt.kbhit():
            msvcrt.getch()
            return True
    except Exception:
        return False
    return False


def drain_dl50(dl50: Dl50Hi, seconds: float = 0.35) -> Dl50Reading:
    end_t = time.perf_counter() + seconds
    latest = Dl50Reading()
    while time.perf_counter() < end_t:
        reading = dl50.read()
        if reading.mm is not None:
            latest = reading
    return latest


def fresh_dl50(dl50: Dl50Hi, settle_s: float = 0.25, samples: int = 3) -> Dl50Reading:
    if hasattr(dl50, "read_fresh"):
        return dl50.read_fresh(settle_s=settle_s, samples=samples)
    return drain_dl50(dl50, seconds=max(0.35, settle_s))


def capture_dl50_lk(dl50: Dl50Hi, lk: LkG3000, label: str) -> tuple[Dl50Reading, LkReading]:
    dl50_reading = fresh_dl50(dl50)
    lk_reading = lk.read()
    print(
        f"[CAPTURE] {label}: "
        f"DL50={format_float(dl50_reading.mm)} "
        f"LK1={format_float(lk_reading.out1_mm)} ({lk_reading.out1_status}) "
        f"LK2={format_float(lk_reading.out2_mm)} ({lk_reading.out2_status})"
    )
    return dl50_reading, lk_reading


def make_row(
    start_t: float,
    angle_group: int,
    angle_deg: float,
    pass_index: int,
    motion_direction: str,
    laser_position: int,
    wheel_position: int,
    trigger_index: int,
    target_delta_mm: float,
    scan_start_mm: float,
    scan_end_mm: float,
    direction: float,
    dl50: Dl50Reading,
    lk: LkReading,
    skipped_targets: int = 0,
    sample_note: str = "",
) -> dict[str, str]:
    dl50_delta = None if dl50.mm is None else dl50.mm - scan_start_mm
    progress = None if dl50.mm is None else direction * (dl50.mm - scan_start_mm)
    return {
        "pc_time": datetime.now().isoformat(timespec="milliseconds"),
        "elapsed_s": f"{time.perf_counter() - start_t:.6f}",
        "angle_group": str(angle_group),
        "angle_deg": f"{angle_deg:.3f}",
        "pass_index": str(pass_index),
        "motion_direction": motion_direction,
        "laser_position": str(laser_position),
        "wheel_position": str(wheel_position),
        "trigger_index": str(trigger_index),
        "target_delta_mm": f"{target_delta_mm:.3f}",
        "scan_start_dl50_mm": format_float(scan_start_mm),
        "scan_end_dl50_mm": format_float(scan_end_mm),
        "dl50_hi_mm": format_float(dl50.mm),
        "dl50_delta_mm": format_float(dl50_delta),
        "dl50_progress_mm": format_float(progress),
        "dl50_raw": dl50.raw,
        "lk_out1_mm": format_float(lk.out1_mm),
        "lk_out2_mm": format_float(lk.out2_mm),
        "lk_out1_status": lk.out1_status,
        "lk_out2_status": lk.out2_status,
        "skipped_targets": str(skipped_targets),
        "sample_note": sample_note,
    }


def open_csv_write(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return path.open("w", newline="", encoding="utf-8-sig"), path
    except PermissionError:
        stamped = path.with_name(f"{path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{path.suffix}")
        print(f"[CSV] Cannot write {path}; using {stamped}.")
        return stamped.open("w", newline="", encoding="utf-8-sig"), stamped


def read_latest_valid(dl50: Dl50Hi, fallback: Dl50Reading) -> Dl50Reading:
    reading = dl50.read()
    if reading.mm is not None:
        return reading
    return fallback


def return_to_start(
    ser,
    dl50: Dl50Hi,
    args: argparse.Namespace,
    start_mm: float,
    direction: float,
    latest: Dl50Reading,
) -> Dl50Reading:
    print()
    print("[RETURN] 始点へ戻ります。戻り中は記録しません。")
    print("[RETURN] 途中停止する場合は何かキーを押してください。")
    latest = fresh_dl50(dl50, settle_s=0.15, samples=2)
    if latest.mm is not None:
        progress = direction * (latest.mm - start_mm)
        if progress <= args.return_tolerance_mm:
            print(f"[RETURN] Already near start. DL50={latest.mm:.3f} progress={progress:.3f} mm")
            stop_wheel(ser, args.wheel_id)
            return latest
    send_servo_position(ser, args.wheel_id, args.wheel_return_speed, "WHEEL")
    deadline = time.perf_counter() + args.max_return_seconds

    try:
        while time.perf_counter() < deadline:
            latest = read_latest_valid(dl50, latest)
            if latest.mm is not None:
                progress = direction * (latest.mm - start_mm)
                print(f"\r[RETURN] DL50={latest.mm:.3f} progress={progress:.3f} mm", end="")
                if progress <= args.return_tolerance_mm:
                    print()
                    print("[RETURN] 始点付近に戻りました。")
                    break
            if key_pressed():
                print()
                print("[RETURN] キー入力を検出したため停止します。")
                break
            time.sleep(args.period)
    finally:
        stop_wheel(ser, args.wheel_id)
    return latest


def scan_one_angle(
    ser,
    dl50: Dl50Hi,
    lk: LkG3000,
    writer: csv.DictWriter,
    csv_file,
    args: argparse.Namespace,
    start_t: float,
    angle_group: int,
    angle_deg: float,
    start_mm: float,
    end_mm: float,
    direction: float,
    latest: Dl50Reading,
) -> Dl50Reading:
    laser_position = angle_to_position(
        angle_deg,
        args.min_deg,
        args.max_deg,
        args.min_pos,
        args.max_pos,
    )
    span_mm = abs(end_mm - start_mm)
    print()
    print("============================================================")
    print(f"[ANGLE] group={angle_group} angle={angle_deg:.3f} deg")
    print(f"[ANGLE] laser position={laser_position}")
    print("============================================================")
    send_servo_position(ser, args.laser_id, laser_position, "LASER")
    time.sleep(args.angle_settle_s)

    latest = return_to_start(ser, dl50, args, start_mm, direction, latest)
    input("[START] 始点位置を確認してください。空Enterでこの角度の測定を開始します > ")

    latest = drain_dl50(dl50)
    if latest.mm is None:
        raise RuntimeError("DL50 start reading is invalid.")
    scan_start_mm = latest.mm
    print(f"[SCAN] actual start DL50={scan_start_mm:.3f} mm")
    print(f"[SCAN] target span={span_mm:.3f} mm, step={args.step_mm:.3f} mm")
    print("[SCAN] 車輪で始点 -> 終点へ移動します。途中停止は何かキー。")

    send_servo_position(ser, args.wheel_id, args.wheel_forward_speed, "WHEEL")
    next_trigger_index = 0 if args.include_zero else 1
    recorded_targets: set[int] = set()
    last_progress_for_record: Optional[float] = None
    deadline = time.perf_counter() + args.max_scan_seconds

    try:
        while time.perf_counter() < deadline:
            latest = read_latest_valid(dl50, latest)
            if latest.mm is None:
                time.sleep(args.period)
                continue

            progress = direction * (latest.mm - scan_start_mm)
            if progress < -args.return_tolerance_mm:
                print(f"\n[WARN] DL50 progress is negative: {progress:.3f} mm. Wheel direction may be wrong.")

            reached_index = math.floor(progress / args.step_mm)
            if reached_index >= next_trigger_index:
                trigger_index = reached_index
                target_delta_mm = trigger_index * args.step_mm
                if target_delta_mm <= span_mm + args.end_tolerance_mm:
                    skipped_targets = max(0, trigger_index - next_trigger_index)
                    target_key = round(target_delta_mm * 1000)
                    same_progress = (
                        last_progress_for_record is not None
                        and abs(progress - last_progress_for_record) < args.min_progress_change_mm
                    )
                    if target_key not in recorded_targets and not same_progress:
                        note = ""
                        if skipped_targets:
                            note = f"skipped_{skipped_targets}_targets"
                            print(
                                f"\n[WARN] DL50 jumped; skipped {skipped_targets} target(s). "
                                f"Consider slower wheel speed."
                            )
                        lk_reading = lk.read()
                        row = make_row(
                            start_t,
                            angle_group,
                            angle_deg,
                            laser_position,
                            args.wheel_forward_speed,
                            trigger_index,
                            target_delta_mm,
                            scan_start_mm,
                            end_mm,
                            direction,
                            latest,
                            lk_reading,
                            skipped_targets=skipped_targets,
                            sample_note=note,
                        )
                        writer.writerow(row)
                        csv_file.flush()
                        recorded_targets.add(target_key)
                        last_progress_for_record = progress
                        print(
                            f"\n[REC] angle={angle_deg:.1f} "
                            f"#{trigger_index} target={target_delta_mm:.1f} "
                            f"progress={progress:.3f} "
                            f"LK1={row['lk_out1_mm']} LK2={row['lk_out2_mm']}"
                        )
                    next_trigger_index = trigger_index + 1

            print(f"\r[SCAN] DL50={latest.mm:.3f} progress={progress:.3f}/{span_mm:.3f} mm", end="")

            if progress >= span_mm - args.end_tolerance_mm:
                print()
                print("[SCAN] 終点に到達しました。")
                break
            if key_pressed():
                print()
                print("[SCAN] キー入力を検出したため停止します。")
                break
            time.sleep(args.period)
    finally:
        stop_wheel(ser, args.wheel_id)

    return latest


def scan_segment(
    ser,
    dl50: Dl50Hi,
    lk: LkG3000,
    writer: csv.DictWriter,
    csv_file,
    args: argparse.Namespace,
    start_t: float,
    angle_group: int,
    angle_deg: float,
    pass_index: int,
    motion_direction: str,
    laser_position: int,
    base_start_mm: float,
    base_end_mm: float,
    direction: float,
    from_progress_mm: float,
    to_progress_mm: float,
    wheel_position: int,
    latest: Dl50Reading,
) -> Dl50Reading:
    span_mm = abs(base_end_mm - base_start_mm)
    travel_span_mm = abs(to_progress_mm - from_progress_mm)
    travel_sign = 1.0 if to_progress_mm >= from_progress_mm else -1.0

    print()
    print(f"[SCAN] {motion_direction}: {from_progress_mm:.3f} -> {to_progress_mm:.3f} mm")
    print(f"[SCAN] step={args.step_mm:.3f} mm, wheel={wheel_position}")
    send_servo_position(ser, args.wheel_id, wheel_position, "WHEEL")

    if travel_sign > 0:
        next_trigger_index = 0 if args.include_zero else max(1, math.floor(from_progress_mm / args.step_mm))
    else:
        next_trigger_index = math.floor(from_progress_mm / args.step_mm)

    recorded_targets: set[int] = set()
    last_progress_for_record: Optional[float] = None
    deadline = time.perf_counter() + args.max_scan_seconds

    try:
        while time.perf_counter() < deadline:
            latest = read_latest_valid(dl50, latest)
            if latest.mm is None:
                time.sleep(args.period)
                continue

            progress = direction * (latest.mm - base_start_mm)
            travel_progress = travel_sign * (progress - from_progress_mm)

            if travel_sign > 0:
                reached_index = math.floor(progress / args.step_mm)
                should_record = reached_index >= next_trigger_index
            else:
                reached_index = math.ceil(progress / args.step_mm)
                should_record = reached_index <= next_trigger_index

            if should_record:
                trigger_index = reached_index
                target_delta_mm = trigger_index * args.step_mm
                if -args.end_tolerance_mm <= target_delta_mm <= span_mm + args.end_tolerance_mm:
                    skipped_targets = abs(trigger_index - next_trigger_index)
                    target_key = round(target_delta_mm * 1000)
                    same_progress = (
                        last_progress_for_record is not None
                        and abs(progress - last_progress_for_record) < args.min_progress_change_mm
                    )
                    if target_key not in recorded_targets and not same_progress:
                        note = motion_direction
                        if skipped_targets:
                            note = f"{motion_direction}_skipped_{skipped_targets}_targets"
                            print(f"\n[WARN] DL50 jumped; skipped {skipped_targets} target(s).")
                        lk_reading = lk.read()
                        row = make_row(
                            start_t,
                            angle_group,
                            angle_deg,
                            pass_index,
                            motion_direction,
                            laser_position,
                            wheel_position,
                            trigger_index,
                            target_delta_mm,
                            base_start_mm,
                            base_end_mm,
                            direction,
                            latest,
                            lk_reading,
                            skipped_targets=skipped_targets,
                            sample_note=note,
                        )
                        writer.writerow(row)
                        csv_file.flush()
                        recorded_targets.add(target_key)
                        last_progress_for_record = progress
                        print(
                            f"\n[REC] {motion_direction} angle={angle_deg:.1f} "
                            f"target={target_delta_mm:.1f} x={progress:.3f} "
                            f"LK1={row['lk_out1_mm']} LK2={row['lk_out2_mm']}"
                        )
                    next_trigger_index = trigger_index + 1 if travel_sign > 0 else trigger_index - 1

            print(
                f"\r[SCAN] {motion_direction} DL50={latest.mm:.3f} "
                f"x={progress:.3f} travel={travel_progress:.3f}/{travel_span_mm:.3f} mm",
                end="",
            )

            if travel_progress >= travel_span_mm - args.end_tolerance_mm:
                print()
                print(f"[SCAN] {motion_direction} reached target.")
                break
            if key_pressed():
                print()
                print(f"[SCAN] Key input detected; stopping {motion_direction}.")
                break
            time.sleep(args.period)
    finally:
        stop_wheel(ser, args.wheel_id)

    return latest


def scan_one_angle(
    ser,
    dl50: Dl50Hi,
    lk: LkG3000,
    writer: csv.DictWriter,
    csv_file,
    args: argparse.Namespace,
    start_t: float,
    angle_group: int,
    angle_deg: float,
    start_mm: float,
    end_mm: float,
    direction: float,
    latest: Dl50Reading,
) -> Dl50Reading:
    laser_position = angle_to_position(
        angle_deg,
        args.min_deg,
        args.max_deg,
        args.min_pos,
        args.max_pos,
    )
    span_mm = abs(end_mm - start_mm)
    print()
    print("============================================================")
    print(f"[ANGLE] group={angle_group} angle={angle_deg:.3f} deg")
    print(f"[ANGLE] laser position={laser_position}")
    print("============================================================")
    send_servo_position(ser, args.laser_id, laser_position, "LASER")
    time.sleep(args.angle_settle_s)

    latest = return_to_start(ser, dl50, args, start_mm, direction, latest)
    input("[START] 始点位置を確認してください。空Enterでこの角度の測定を開始します > ")

    latest = drain_dl50(dl50)
    if latest.mm is None:
        raise RuntimeError("DL50 start reading is invalid.")
    print(f"[SCAN] actual start DL50={latest.mm:.3f} mm")
    print(f"[SCAN] target span={span_mm:.3f} mm, step={args.step_mm:.3f} mm")
    print("[SCAN] start -> end を記録します。途中停止は何かキーを押してください。")

    latest = scan_segment(
        ser,
        dl50,
        lk,
        writer,
        csv_file,
        args,
        start_t,
        angle_group,
        angle_deg,
        1,
        "forward",
        laser_position,
        start_mm,
        end_mm,
        direction,
        0.0,
        span_mm,
        args.wheel_forward_speed,
        latest,
    )

    if args.record_return:
        print("[SCAN] end -> start も記録します。")
        latest = scan_segment(
            ser,
            dl50,
            lk,
            writer,
            csv_file,
            args,
            start_t,
            angle_group,
            angle_deg,
            2,
            "return",
            laser_position,
            start_mm,
            end_mm,
            direction,
            span_mm,
            0.0,
            args.wheel_return_speed,
            latest,
        )

    return latest


def angle_sequence(start: float, end: float, step: float) -> list[float]:
    if step == 0:
        raise ValueError("angle step must not be zero")
    if start < end and step < 0:
        step = -step
    if start > end and step > 0:
        step = -step

    values: list[float] = []
    current = start
    if step > 0:
        while current <= end + 1e-9:
            values.append(round(current, 6))
            current += step
    else:
        while current >= end - 1e-9:
            values.append(round(current, 6))
            current += step
    return values


def run(args: argparse.Namespace) -> None:
    script_dir = Path(__file__).resolve().parent
    lk = LkG3000(script_dir / "LkIF.dll")
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
        print(f"[SERVO] laser ID={args.laser_id}, wheel ID={args.wheel_id}")
        lk.open()
        dl50.open()

        if args.record_return:
            # Return pass must move in the opposite physical direction. Use the
            # mirrored ICS value around the stop position so the speed magnitude
            # stays the same.
            args.wheel_return_speed = STOP_POSITION + (STOP_POSITION - args.wheel_forward_speed)

        print()
        print("============================================================")
        print("自動スキャン測定を開始します")
        print("角度: -90〜90 deg, 5 deg間隔")
        print("軸方向: 1 mm間隔")
        print("測定方向: 始点 -> 終点のみ記録、戻りは記録しません")
        print("============================================================")
        print()
        print("1. 装置を測定の始点へ移動してください。")
        print("   手で動かせるように、車輪サーボをfreeにします。")
        free_wheel(ser, args.wheel_id)
        input("   始点に合わせたら空Enterを押してください > ")
        start_reading, _ = capture_dl50_lk(dl50, lk, "start")
        if start_reading.mm is None:
            raise RuntimeError("DL50 start value is invalid.")

        print()
        print("2. 装置を測定の終点へ移動してください。")
        print("   車輪サーボはfreeのままです。手で終点へ合わせてください。")
        input("   終点に合わせたら空Enterを押してください > ")
        end_reading, _ = capture_dl50_lk(dl50, lk, "end")
        if end_reading.mm is None:
            raise RuntimeError("DL50 end value is invalid.")

        start_mm = start_reading.mm
        end_mm = end_reading.mm
        span_mm = abs(end_mm - start_mm)
        if span_mm < args.step_mm:
            raise RuntimeError("Start/end span is smaller than step size.")
        direction = 1.0 if end_mm >= start_mm else -1.0
        latest = end_reading

        print()
        print(f"[BASE] start={start_mm:.3f} mm, end={end_mm:.3f} mm, span={span_mm:.3f} mm")
        print(f"[BASE] DL50 direction sign={direction:+.0f}")
        print(f"[WHEEL] forward={args.wheel_forward_speed}, return={args.wheel_return_speed}, stop=7500")
        print("[WHEEL] 測定開始前に車輪サーボを停止状態へ戻します。")
        stop_wheel(ser, args.wheel_id)
        input("[READY] 測定を開始してよければ空Enterを押してください > ")

        csv_file, actual_csv = open_csv_write(Path(args.csv))
        print(f"[CSV] Writing: {actual_csv.resolve()}")
        start_t = time.perf_counter()

        with csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
            writer.writeheader()

            for angle_group, angle in enumerate(
                angle_sequence(args.angle_start, args.angle_end, args.angle_step),
                start=1,
            ):
                latest = scan_one_angle(
                    ser,
                    dl50,
                    lk,
                    writer,
                    csv_file,
                    args,
                    start_t,
                    angle_group,
                    angle,
                    start_mm,
                    end_mm,
                    direction,
                    latest,
                )
                if args.pause_each_angle:
                    input("[PAUSE] 次の角度へ進むには空Enterを押してください > ")

        print("[RUN] All angles finished.")

    except KeyboardInterrupt:
        print("\n[RUN] Ctrl+C received. Stopping safely.")
    finally:
        if ser is not None:
            try:
                stop_wheel(ser, args.wheel_id)
                send_servo_position(ser, args.laser_id, 7500, "LASER")
                ser.close()
            except Exception:
                pass
        dl50.close()
        lk.close()
        print("[RUN] Finished.")


def run(args: argparse.Namespace) -> None:
    script_dir = Path(__file__).resolve().parent
    lk = LkG3000(script_dir / "LkIF.dll")
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
        print(f"[SERVO] laser ID={args.laser_id}, wheel ID={args.wheel_id}")
        lk.open()
        dl50.open()

        print()
        print("============================================================")
        print("自動スキャン測定を開始します")
        print(f"角度: {args.angle_start:g} -> {args.angle_end:g} deg, step={args.angle_step:g} deg")
        print(f"軸方向: step={args.step_mm:g} mm")
        print(f"戻り測定: {'ON' if args.record_return else 'OFF'}")
        print("============================================================")

        if args.fixed_start_mm is not None and args.fixed_end_mm is not None:
            start_mm = args.fixed_start_mm
            end_mm = args.fixed_end_mm
            latest = fresh_dl50(dl50, settle_s=0.25, samples=3)
            if latest.mm is None:
                raise RuntimeError("DL50 current value is invalid.")
            print(f"[BASE] 固定始点/終点を使います: start={start_mm:.3f} mm, end={end_mm:.3f} mm")
            print(f"[BASE] 現在のDL50={latest.mm:.3f} mm。各角度の前に始点へ自動で戻ります。")
        else:
            print()
            print("1. 装置を測定の始点へ移動してください。")
            print("   手で動かせるように、車輪サーボをfreeにします。")
            free_wheel(ser, args.wheel_id)
            input("   始点に合わせたら空Enterを押してください > ")
            start_reading, _ = capture_dl50_lk(dl50, lk, "start")
            if start_reading.mm is None:
                raise RuntimeError("DL50 start value is invalid.")

            print()
            print("2. 装置を測定の終点へ移動してください。")
            print("   車輪サーボはfreeのままです。手で終点へ合わせてください。")
            input("   終点に合わせたら空Enterを押してください > ")
            end_reading, _ = capture_dl50_lk(dl50, lk, "end")
            if end_reading.mm is None:
                raise RuntimeError("DL50 end value is invalid.")

            start_mm = start_reading.mm
            end_mm = end_reading.mm
            latest = end_reading

        span_mm = abs(end_mm - start_mm)
        if span_mm < args.step_mm:
            raise RuntimeError("Start/end span is smaller than step size.")
        direction = 1.0 if end_mm >= start_mm else -1.0

        print()
        print(f"[BASE] start={start_mm:.3f} mm, end={end_mm:.3f} mm, span={span_mm:.3f} mm")
        print(f"[BASE] DL50 direction sign={direction:+.0f}")
        print(f"[WHEEL] forward={args.wheel_forward_speed}, return={args.wheel_return_speed}, stop=7500")
        stop_wheel(ser, args.wheel_id)
        input("[READY] 測定を開始してよければ空Enterを押してください > ")

        csv_file, actual_csv = open_csv_write(Path(args.csv))
        print(f"[CSV] Writing: {actual_csv.resolve()}")
        start_t = time.perf_counter()

        with csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=CSV_COLUMNS)
            writer.writeheader()

            for angle_group, angle in enumerate(
                angle_sequence(args.angle_start, args.angle_end, args.angle_step),
                start=1,
            ):
                latest = scan_one_angle(
                    ser,
                    dl50,
                    lk,
                    writer,
                    csv_file,
                    args,
                    start_t,
                    angle_group,
                    angle,
                    start_mm,
                    end_mm,
                    direction,
                    latest,
                )
                if args.pause_each_angle:
                    input("[PAUSE] 次の角度へ進むには空Enterを押してください > ")

        print("[RUN] All angles finished.")

    except KeyboardInterrupt:
        print("\n[RUN] Ctrl+C received. Stopping safely.")
    finally:
        if ser is not None:
            try:
                stop_wheel(ser, args.wheel_id)
                send_servo_position(ser, args.laser_id, 7500, "LASER")
                ser.close()
            except Exception:
                pass
        dl50.close()
        lk.close()
        print("[RUN] Finished.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Automatic -90..90 deg, 1 mm wheel scan logger.")
    parser.add_argument("--dl50-port", default=DEFAULT_DL50_PORT)
    parser.add_argument("--dl50-baud", type=int, default=115200)
    parser.add_argument("--dl50-bytesize", type=int, choices=[7, 8], default=7)
    parser.add_argument("--dl50-parity", choices=["N", "E", "O"], default="E")
    parser.add_argument("--dl50-stopbits", type=float, choices=[1, 1.5, 2], default=1)
    parser.add_argument("--servo-port", default=DEFAULT_SERVO_PORT)
    parser.add_argument("--servo-baud", type=int, default=115200)
    parser.add_argument("--laser-id", type=int, default=5)
    parser.add_argument("--wheel-id", type=int, default=4)
    parser.add_argument("--wheel-forward-speed", type=int, default=7600)
    parser.add_argument("--wheel-return-speed", type=int, default=11500)
    parser.add_argument("--angle-start", type=float, default=-90.0)
    parser.add_argument("--angle-end", type=float, default=90.0)
    parser.add_argument("--angle-step", type=float, default=5.0)
    parser.add_argument("--step-mm", type=float, default=1.0)
    parser.add_argument("--fixed-start-mm", type=float, help="Use this DL50 value as the scan start instead of manual capture.")
    parser.add_argument("--fixed-end-mm", type=float, help="Use this DL50 value as the scan end instead of manual capture.")
    parser.add_argument("--record-return", action="store_true", help="Also record LK/DL50 while moving end -> start.")
    parser.add_argument("--period", type=float, default=0.01)
    parser.add_argument(
        "--min-progress-change-mm",
        type=float,
        default=0.2,
        help="Do not record another row if DL50 progress has not changed by at least this amount.",
    )
    parser.add_argument("--angle-settle-s", type=float, default=0.25)
    parser.add_argument("--return-tolerance-mm", type=float, default=2.0)
    parser.add_argument("--end-tolerance-mm", type=float, default=0.5)
    parser.add_argument("--max-scan-seconds", type=float, default=300.0)
    parser.add_argument("--max-return-seconds", type=float, default=300.0)
    parser.add_argument("--include-zero", action="store_true", help="Also record target_delta_mm=0 at each angle.")
    parser.add_argument("--pause-each-angle", action="store_true")
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--min-deg", type=float, default=DEFAULT_MIN_DEG)
    parser.add_argument("--max-deg", type=float, default=DEFAULT_MAX_DEG)
    parser.add_argument("--min-pos", type=int, default=DEFAULT_MIN_POS)
    parser.add_argument("--max-pos", type=int, default=DEFAULT_MAX_POS)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.step_mm <= 0:
        print("--step-mm must be greater than zero.")
        return 2
    if args.period <= 0:
        print("--period must be greater than zero.")
        return 2
    if args.angle_step == 0:
        print("--angle-step must not be zero.")
        return 2
    for name in ("wheel_forward_speed", "wheel_return_speed"):
        value = getattr(args, name)
        if not 0 <= value <= 11500:
            print(f"--{name.replace('_', '-')} must be between 0 and 11500.")
            return 2

    try:
        import serial  # noqa: F401
    except ImportError:
        print("pyserial is not installed. Install with: pip install pyserial")
        return 1

    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
