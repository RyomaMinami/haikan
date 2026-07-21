"""
Angle-by-angle step trigger logger.

Controls a KONDO KRS-9004 via Dual USB Adapter HS, then records LK-G values
every time DL50 distance increases by a fixed step from the Enter baseline.

Workflow:
    1. Start this script.
    2. Type an angle, for example: 30
    3. Press Enter at the start point.
    4. Move to the end point and press Enter again.
    5. The script calculates angle-specific slope correction.
    6. Return to the start point and move forward. Each +5 mm DL50 step records
       raw and corrected LK-G values.
    7. Type another angle, for example: -15, and repeat.
    6. All angle groups are appended to the same CSV.

Commands:
    <number>  Move servo to angle in degrees.
    c         Move servo to 0 deg.
    f         Servo free.
    Enter     Capture start/end points, then start a new logging group.
    q         Quit.
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
DEFAULT_SERVO_PORT = "COM8"
DEFAULT_SERVO_ID = 5
DEFAULT_STEP_MM = 5.0
DEFAULT_PERIOD_S = 0.02
DEFAULT_CSV = "angle_step_trigger_log.csv"

DEFAULT_MIN_DEG = -135.0
DEFAULT_MAX_DEG = 135.0
DEFAULT_MIN_POS = 3500
DEFAULT_MAX_POS = 11500


CSV_COLUMNS = [
    "pc_time",
    "elapsed_s",
    "angle_group",
    "angle_deg",
    "servo_position",
    "trigger_index",
    "target_delta_mm",
    "dl50_initial_mm",
    "dl50_hi_mm",
    "dl50_delta_mm",
    "dl50_raw",
    "lk_out1_mm",
    "lk_out2_mm",
    "lk_out1_corrected_mm",
    "lk_out2_corrected_mm",
    "lk_out1_slope_drift_mm",
    "lk_out2_slope_drift_mm",
    "slope_span_mm",
    "slope_out1_start_mm",
    "slope_out1_end_mm",
    "slope_out2_start_mm",
    "slope_out2_end_mm",
    "lk_out1_status",
    "lk_out2_status",
]


class SlopeCorrection:
    def __init__(
        self,
        span_mm: float,
        out1_start_mm: float,
        out1_end_mm: float,
        out2_start_mm: float,
        out2_end_mm: float,
    ) -> None:
        self.span_mm = span_mm
        self.out1_start_mm = out1_start_mm
        self.out1_end_mm = out1_end_mm
        self.out2_start_mm = out2_start_mm
        self.out2_end_mm = out2_end_mm

    def drift(self, delta_mm: Optional[float]) -> tuple[Optional[float], Optional[float]]:
        if delta_mm is None or self.span_mm == 0:
            return None, None
        ratio = delta_mm / self.span_mm
        return (
            ratio * (self.out1_end_mm - self.out1_start_mm),
            ratio * (self.out2_end_mm - self.out2_start_mm),
        )


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
    position = max(0, min(11500, position))
    return bytes(
        [
            0x80 | (servo_id & 0x1F),
            (position >> 7) & 0x7F,
            position & 0x7F,
        ]
    )


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


def send_servo_position(ser, servo_id: int, position: int) -> None:
    command = encode_ics_position_command(servo_id, position)
    ser.reset_input_buffer()
    ser.write(command)
    ser.flush()
    time.sleep(0.02)
    response = ser.read(16)
    rx = " ".join(f"{b:02X}" for b in response)
    tx = " ".join(f"{b:02X}" for b in command)
    print(f"[SERVO] position={position} TX={tx} RX={rx}")


def open_csv_append(csv_path: Path):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    exists = csv_path.exists() and csv_path.stat().st_size > 0
    try:
        f = csv_path.open("a", newline="", encoding="utf-8-sig")
        return f, csv_path, exists
    except PermissionError:
        stamped_path = csv_path.with_name(
            f"{csv_path.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{csv_path.suffix}"
        )
        print(f"[CSV] Cannot write {csv_path}; using {stamped_path} instead.")
        exists = stamped_path.exists() and stamped_path.stat().st_size > 0
        f = stamped_path.open("a", newline="", encoding="utf-8-sig")
        return f, stamped_path, exists


def make_row(
    start_t: float,
    angle_group: int,
    angle_deg: Optional[float],
    servo_position: Optional[int],
    trigger_index: int,
    target_delta_mm: float,
    initial_mm: float,
    dl50: Dl50Reading,
    lk: LkReading,
    slope: Optional[SlopeCorrection],
) -> dict[str, str]:
    dl50_delta = None if dl50.mm is None else dl50.mm - initial_mm
    drift1, drift2 = (None, None) if slope is None else slope.drift(dl50_delta)
    out1_corrected = (
        None if lk.out1_mm is None or drift1 is None else lk.out1_mm - drift1
    )
    out2_corrected = (
        None if lk.out2_mm is None or drift2 is None else lk.out2_mm - drift2
    )
    return {
        "pc_time": datetime.now().isoformat(timespec="milliseconds"),
        "elapsed_s": f"{time.perf_counter() - start_t:.6f}",
        "angle_group": str(angle_group),
        "angle_deg": "" if angle_deg is None else f"{angle_deg:.3f}",
        "servo_position": "" if servo_position is None else str(servo_position),
        "trigger_index": str(trigger_index),
        "target_delta_mm": f"{target_delta_mm:.3f}",
        "dl50_initial_mm": format_float(initial_mm),
        "dl50_hi_mm": format_float(dl50.mm),
        "dl50_delta_mm": format_float(dl50_delta),
        "dl50_raw": dl50.raw,
        "lk_out1_mm": format_float(lk.out1_mm),
        "lk_out2_mm": format_float(lk.out2_mm),
        "lk_out1_corrected_mm": format_float(out1_corrected),
        "lk_out2_corrected_mm": format_float(out2_corrected),
        "lk_out1_slope_drift_mm": format_float(drift1),
        "lk_out2_slope_drift_mm": format_float(drift2),
        "slope_span_mm": "" if slope is None else format_float(slope.span_mm),
        "slope_out1_start_mm": "" if slope is None else format_float(slope.out1_start_mm),
        "slope_out1_end_mm": "" if slope is None else format_float(slope.out1_end_mm),
        "slope_out2_start_mm": "" if slope is None else format_float(slope.out2_start_mm),
        "slope_out2_end_mm": "" if slope is None else format_float(slope.out2_end_mm),
        "lk_out1_status": lk.out1_status,
        "lk_out2_status": lk.out2_status,
    }


def drain_dl50(dl50: Dl50Hi, seconds: float = 0.3) -> Dl50Reading:
    end_t = time.perf_counter() + seconds
    latest = Dl50Reading()
    while time.perf_counter() < end_t:
        reading = dl50.read()
        if reading.mm is not None:
            latest = reading
    return latest


def capture_point(dl50: Dl50Hi, lk: LkG3000, label: str) -> tuple[Dl50Reading, LkReading]:
    dl50_reading = drain_dl50(dl50)
    lk_reading = lk.read()
    print(
        f"[CAL] {label}: "
        f"DL50={format_float(dl50_reading.mm)} "
        f"LK1={format_float(lk_reading.out1_mm)} ({lk_reading.out1_status}) "
        f"LK2={format_float(lk_reading.out2_mm)} ({lk_reading.out2_status})"
    )
    return dl50_reading, lk_reading


def prompt_float(label: str) -> float:
    while True:
        text = input(f"{label}: ").strip()
        try:
            return float(text)
        except ValueError:
            print("Enter a numeric value.")


def build_slope_correction(args: argparse.Namespace) -> Optional[SlopeCorrection]:
    if not args.slope_correct:
        return None

    span = args.slope_span_mm
    out1_start = args.slope_out1_start
    out1_end = args.slope_out1_end
    out2_start = args.slope_out2_start
    out2_end = args.slope_out2_end

    print("[SLOPE] Enter physical tilt correction values.")
    print("[SLOPE] span_mm is the DL50 movement distance from start to end.")
    if span is None:
        span = prompt_float("span_mm")
    if out1_start is None:
        out1_start = prompt_float("OUT1 start LK-G mm")
    if out1_end is None:
        out1_end = prompt_float("OUT1 end LK-G mm")
    if out2_start is None:
        out2_start = prompt_float("OUT2 start LK-G mm")
    if out2_end is None:
        out2_end = prompt_float("OUT2 end LK-G mm")

    correction = SlopeCorrection(span, out1_start, out1_end, out2_start, out2_end)
    print(
        "[SLOPE] enabled: "
        f"OUT1 drift {out1_end - out1_start:.6f} mm / {span:.6f} mm, "
        f"OUT2 drift {out2_end - out2_start:.6f} mm / {span:.6f} mm"
    )
    return correction


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
    typed_slope = build_slope_correction(args)

    servo = None
    try:
        servo = open_servo_serial(args.servo_port, args.servo_baud)
        print(
            f"[SERVO] Opened {args.servo_port} at {args.servo_baud} bps, "
            f"8E1, ID={args.servo_id}"
        )
    except Exception as exc:
        print(f"[SERVO] Failed to open {args.servo_port}: {exc}")
        print("[SERVO] Continue without servo control. Angle labels can still be typed.")

    lk.open()
    dl50.open()

    f, actual_csv_path, has_header = open_csv_append(Path(args.csv))
    print(f"[CSV] Appending: {actual_csv_path.resolve()}")
    print()
    print("============================================================")
    print("角度ごとの 5mm 間隔測定を開始します")
    print("================================================------------")
    print("手順:")
    print("  1. 測定したい角度を入力してください。例: 0, 5, -10")
    print("  2. サーボが動いたら、装置を始点に合わせて空Enterを押してください。")
    print("  3. 装置を終点に動かして、空Enterを押してください。")
    print("  4. 傾き補正を計算します。")
    print("  5. 装置を始点へ戻してください。戻したら自動で5mmごとに記録します。")
    print("  6. 記録中に何かキーを押すと、その角度の記録を終了して角度入力へ戻ります。")
    print()
    print("コマンド:")
    print("  c : 0度へ移動")
    print("  f : サーボfree")
    print("  q : 終了")
    print("============================================================")
    print()

    current_angle: Optional[float] = None
    current_position: Optional[int] = None
    initial_mm: Optional[float] = None
    next_trigger_index = 1
    angle_group = 0
    last_dl50 = Dl50Reading()
    recorded_dl50_keys: set[int] = set()
    pending_start: Optional[tuple[Dl50Reading, LkReading]] = None
    current_slope: Optional[SlopeCorrection] = typed_slope
    start_t = time.perf_counter()

    try:
        with f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            if not has_header:
                writer.writeheader()

            while True:
                text = input("[角度入力] 角度を入力してください / qで終了 > ").strip()
                if text.lower() == "q":
                    break

                if text.lower() == "f":
                    if servo is not None:
                        send_servo_position(servo, args.servo_id, 0)
                    current_position = 0
                    continue

                if text.lower() == "c":
                    text = "0"

                if text == "":
                    if current_angle is None:
                        print("[案内] 先に角度を入力してください。例: 0")
                        continue

                    if pending_start is None:
                        print("[案内] 始点を記録します。装置が始点にあることを確認してください。")
                        pending_start = capture_point(dl50, lk, "start")
                        print()
                        print("------------------------------------------------------------")
                        print("次に、装置を終点まで動かしてください。")
                        print("終点に到達したら、もう一度 空Enter を押してください。")
                        print("------------------------------------------------------------")
                        print()
                        continue

                    print("[案内] 終点を記録します。装置が終点にあることを確認してください。")
                    end_dl50, end_lk = capture_point(dl50, lk, "end")
                    start_dl50, start_lk = pending_start
                    pending_start = None

                    if (
                        start_dl50.mm is None
                        or end_dl50.mm is None
                        or start_lk.out1_mm is None
                        or end_lk.out1_mm is None
                        or start_lk.out2_mm is None
                        or end_lk.out2_mm is None
                    ):
                        print("[エラー] 傾き補正を計算できません。DL50またはLK-Gの値が無効です。")
                        continue

                    span_mm = end_dl50.mm - start_dl50.mm
                    if span_mm == 0:
                        print("[エラー] 始点と終点のDL50値が同じです。終点をもっと離してください。")
                        continue

                    current_slope = SlopeCorrection(
                        span_mm=span_mm,
                        out1_start_mm=start_lk.out1_mm,
                        out1_end_mm=end_lk.out1_mm,
                        out2_start_mm=start_lk.out2_mm,
                        out2_end_mm=end_lk.out2_mm,
                    )
                    angle_group += 1
                    print(
                        f"[BASE] group={angle_group} angle={current_angle} "
                        f"cal_start={start_dl50.mm:.6f} mm span={span_mm:.6f} mm"
                    )
                    print(
                        f"[SLOPE] OUT1 drift={end_lk.out1_mm - start_lk.out1_mm:.6f} "
                        f"OUT2 drift={end_lk.out2_mm - start_lk.out2_mm:.6f}"
                    )
                    print()
                    print("============================================================")
                    print("傾き補正の計算が完了しました。")
                    print("装置を始点へ戻してください。")
                    print("始点へ戻したら、空Enterを押してください。")
                    print("空Enterを押した時点のDL50値を、計測用の初期値として記録します。")
                    print("その後、測定方向へ動かしてください。")
                    print("DL50が5mm増えるごとにLK-G値をCSVへ記録します。")
                    print("この角度の測定を終える場合は、何かキーを押してください。")
                    print("============================================================")
                    print()

                    start_text = input("[計測開始] 始点へ戻したら空Enterを押してください > ").strip()
                    if start_text.lower() == "q":
                        break
                    if start_text != "":
                        print("[案内] 空Enterではなかったため、計測開始をキャンセルします。")
                        print("[案内] 次の角度を入力してください。")
                        continue

                    measurement_start = drain_dl50(dl50)
                    if measurement_start.mm is None:
                        print("[エラー] 計測開始時のDL50値が無効です。やり直してください。")
                        continue

                    initial_mm = measurement_start.mm
                    last_dl50 = measurement_start
                    next_trigger_index = 1
                    recorded_dl50_keys = set()
                    print(f"[START] measurement initial={initial_mm:.6f} mm")

                    while True:
                        reading = dl50.read()
                        if reading.mm is not None:
                            last_dl50 = reading

                        key_available = False
                        try:
                            import msvcrt

                            key_available = msvcrt.kbhit()
                        except Exception:
                            key_available = False

                        if key_available:
                            print()
                            print("[案内] キー入力を検出しました。この角度の記録を終了します。")
                            print("[案内] 次の角度を入力してください。終了する場合は q。")
                            break

                        if initial_mm is not None and last_dl50.mm is not None:
                            delta_mm = last_dl50.mm - initial_mm
                            reached_index = math.floor(delta_mm / args.step_mm)
                            dl50_key = round(last_dl50.mm * 1000)
                            if (
                                reached_index >= next_trigger_index
                                and dl50_key not in recorded_dl50_keys
                            ):
                                trigger_index = reached_index
                                target_delta_mm = trigger_index * args.step_mm
                                lk_reading = lk.read()
                                row = make_row(
                                    start_t,
                                    angle_group,
                                    current_angle,
                                    current_position,
                                    trigger_index,
                                    target_delta_mm,
                                    initial_mm,
                                    last_dl50,
                                    lk_reading,
                                    current_slope,
                                )
                                writer.writerow(row)
                                f.flush()
                                recorded_dl50_keys.add(dl50_key)
                                print(
                                    f"[TRIG g={angle_group} #{trigger_index}] "
                                    f"angle={row['angle_deg']} "
                                    f"delta={row['dl50_delta_mm']} "
                                    f"LK1={row['lk_out1_corrected_mm']} "
                                    f"LK2={row['lk_out2_corrected_mm']}"
                                )
                                next_trigger_index = trigger_index + 1
                                if (
                                    args.count_per_angle is not None
                                    and len(recorded_dl50_keys) >= args.count_per_angle
                                ):
                                    print("[案内] この角度の指定記録数に到達しました。")
                                    key_available = True
                            if key_available:
                                break

                        time.sleep(args.period)
                    continue

                try:
                    angle = float(text)
                except ValueError:
                    print("[案内] 角度の数字、空Enter、c、f、q のいずれかを入力してください。")
                    continue

                current_angle = angle
                current_position = angle_to_position(
                    angle,
                    args.min_deg,
                    args.max_deg,
                    args.min_pos,
                    args.max_pos,
                )
                print()
                print("------------------------------------------------------------")
                print(f"角度 {current_angle:.3f} deg に移動します。servo position={current_position}")
                if servo is not None:
                    send_servo_position(servo, args.servo_id, current_position)
                print("装置を始点に合わせてください。")
                print("始点に合わせたら、空Enterを押してください。")
                print("------------------------------------------------------------")
                print()

    except KeyboardInterrupt:
        print("\n[RUN] Ctrl+C received. Stopping safely.")
    finally:
        dl50.close()
        lk.close()
        if servo is not None:
            try:
                send_servo_position(servo, args.servo_id, 7500)
                servo.close()
            except Exception:
                pass
        print("[RUN] Finished.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Angle grouped DL50/LK-G step logger.")
    parser.add_argument("--dl50-port", default=DEFAULT_DL50_PORT)
    parser.add_argument("--dl50-baud", type=int, default=115200)
    parser.add_argument("--dl50-bytesize", type=int, choices=[7, 8], default=7)
    parser.add_argument("--dl50-parity", choices=["N", "E", "O"], default="E")
    parser.add_argument("--dl50-stopbits", type=float, choices=[1, 1.5, 2], default=1)
    parser.add_argument("--servo-port", default=DEFAULT_SERVO_PORT)
    parser.add_argument("--servo-baud", type=int, default=115200)
    parser.add_argument("--servo-id", type=int, default=DEFAULT_SERVO_ID)
    parser.add_argument("--min-deg", type=float, default=DEFAULT_MIN_DEG)
    parser.add_argument("--max-deg", type=float, default=DEFAULT_MAX_DEG)
    parser.add_argument("--min-pos", type=int, default=DEFAULT_MIN_POS)
    parser.add_argument("--max-pos", type=int, default=DEFAULT_MAX_POS)
    parser.add_argument("--step-mm", type=float, default=DEFAULT_STEP_MM)
    parser.add_argument("--period", type=float, default=DEFAULT_PERIOD_S)
    parser.add_argument("--count-per-angle", type=int, default=None)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument(
        "--slope-correct",
        action="store_true",
        help="Enable live linear tilt correction from typed start/end LK-G values.",
    )
    parser.add_argument("--slope-span-mm", type=float, default=None)
    parser.add_argument("--slope-out1-start", type=float, default=None)
    parser.add_argument("--slope-out1-end", type=float, default=None)
    parser.add_argument("--slope-out2-start", type=float, default=None)
    parser.add_argument("--slope-out2-end", type=float, default=None)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    if args.min_deg >= args.max_deg:
        print("--min-deg must be smaller than --max-deg.")
        return 2
    if args.step_mm <= 0:
        print("--step-mm must be greater than zero.")
        return 2
    if args.period <= 0:
        print("--period must be greater than zero.")
        return 2
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
