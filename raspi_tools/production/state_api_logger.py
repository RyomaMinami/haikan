#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import signal
import time
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen


DEFAULT_FIELDS = [
    "pc_time",
    "elapsed_s",
    "api_ok",
    "imu_ax_g",
    "imu_ay_g",
    "imu_az_g",
    "sensor_distance_mm",
    "sensor_pressure_mpa",
    "sensor_grinder_rpm",
    "motor_state",
    "motor_duty",
    "motor_enabled",
    "motor_current_a",
    "motor_current_v",
    "motor_rpm",
    "motor_encoder_rpm",
    "motor_step_hz",
    "motor_adc27_v",
    "motor_adc32_v",
    "motor_step_sense_a_v",
    "motor_step_sense_b_v",
    "valve_move_push",
    "valve_move_pull",
    "valve_drill_push",
    "valve_drill_pull",
    "valve_grinder_air",
    "controller_source_ip",
    "controller_pressed_buttons",
    "controller_axes",
    "raw_json",
]


STOP = False


def handle_stop(signum, frame) -> None:  # noqa: ANN001
    global STOP
    STOP = True


def dig(data: dict, *keys: str):
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def compact(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def make_row(state: dict, start_time: float) -> dict[str, str]:
    now = time.time()
    motor = state.get("motor", {}) if isinstance(state, dict) else {}
    sensors = state.get("sensors", {}) if isinstance(state, dict) else {}
    imu = state.get("imu", {}) if isinstance(state, dict) else {}
    valves = state.get("valves", {}) if isinstance(state, dict) else {}
    controller = state.get("controller", {}) if isinstance(state, dict) else {}

    row = {
        "pc_time": datetime.now().isoformat(timespec="milliseconds"),
        "elapsed_s": f"{now - start_time:.3f}",
        "api_ok": "1",
        "imu_ax_g": compact(imu.get("ax_g")),
        "imu_ay_g": compact(imu.get("ay_g")),
        "imu_az_g": compact(imu.get("az_g")),
        "sensor_distance_mm": compact(sensors.get("distance_mm")),
        "sensor_pressure_mpa": compact(sensors.get("pressure_mpa")),
        "sensor_grinder_rpm": compact(sensors.get("grinder_rpm")),
        "motor_state": compact(motor.get("state")),
        "motor_duty": compact(motor.get("duty")),
        "motor_enabled": compact(motor.get("enabled")),
        "motor_current_a": compact(motor.get("current_a")),
        "motor_current_v": compact(motor.get("current_v")),
        "motor_rpm": compact(motor.get("rpm")),
        "motor_encoder_rpm": compact(motor.get("encoder_rpm")),
        "motor_step_hz": compact(motor.get("step_hz")),
        "motor_adc27_v": compact(motor.get("adc27_v")),
        "motor_adc32_v": compact(motor.get("adc32_v")),
        "motor_step_sense_a_v": compact(motor.get("step_sense_a_v")),
        "motor_step_sense_b_v": compact(motor.get("step_sense_b_v")),
        "valve_move_push": compact(valves.get("move_push")),
        "valve_move_pull": compact(valves.get("move_pull")),
        "valve_drill_push": compact(valves.get("drill_push")),
        "valve_drill_pull": compact(valves.get("drill_pull")),
        "valve_grinder_air": compact(valves.get("grinder_air")),
        "controller_source_ip": compact(controller.get("source_ip")),
        "controller_pressed_buttons": compact(controller.get("pressed_buttons")),
        "controller_axes": compact(controller.get("axes")),
        "raw_json": json.dumps(state, ensure_ascii=False, separators=(",", ":")),
    }
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="Record pipe robot dashboard API state to CSV and JSONL.")
    parser.add_argument("--url", default="http://127.0.0.1:8090/api/state")
    parser.add_argument("--output-dir", default="/home/haikan/pipe_robot_logs/production")
    parser.add_argument("--interval", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--prefix", default="state")
    args = parser.parse_args()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = outdir / f"{args.prefix}_{stamp}.csv"
    jsonl_path = outdir / f"{args.prefix}_{stamp}.jsonl"

    start = time.time()
    print(f"[state-log] csv={csv_path}")
    print(f"[state-log] jsonl={jsonl_path}")

    with csv_path.open("w", newline="", encoding="utf-8-sig") as csv_file, jsonl_path.open(
        "w", encoding="utf-8"
    ) as jsonl_file:
        writer = csv.DictWriter(csv_file, fieldnames=DEFAULT_FIELDS)
        writer.writeheader()

        while not STOP:
            loop_start = time.time()
            try:
                with urlopen(args.url, timeout=args.timeout) as response:
                    state = json.loads(response.read().decode("utf-8"))
                row = make_row(state, start)
                jsonl_file.write(json.dumps(state, ensure_ascii=False) + "\n")
            except Exception as exc:
                row = {field: "" for field in DEFAULT_FIELDS}
                row.update(
                    {
                        "pc_time": datetime.now().isoformat(timespec="milliseconds"),
                        "elapsed_s": f"{time.time() - start:.3f}",
                        "api_ok": "0",
                        "raw_json": f"logger_error={type(exc).__name__}: {exc}",
                    }
                )
                print(f"[state-log] API error: {exc}")

            writer.writerow(row)
            csv_file.flush()
            jsonl_file.flush()

            sleep_s = args.interval - (time.time() - loop_start)
            if sleep_s > 0:
                time.sleep(sleep_s)

    print("[state-log] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
