from __future__ import annotations

import argparse
import csv
import re
import signal
import time
from datetime import datetime
from pathlib import Path

import serial


FIELDS = [
    "pc_time",
    "elapsed_s",
    "phase",
    "command_abs_y",
    "command_abs_x",
    "raw",
    "esp_ms",
    "motor_id",
    "rpm",
    "current_a",
    "current_v",
    "speed_v",
    "encoder_rpm",
    "adc27_v",
    "adc32_v",
    "adc33_v",
    "adc34_v",
    "adc35_v",
    "adc36_v",
    "adc39_v",
    "step_sense_a_v",
    "step_sense_b_v",
    "step_sense_a_a",
    "step_sense_b_a",
    "step_current_abs_a",
    "voltage_v",
    "duty",
    "enabled",
    "step_hz",
    "estop",
    "state",
]


def coerce_value(value: str) -> object:
    try:
        if re.fullmatch(r"[-+]?\d+", value):
            return int(value)
        return float(value)
    except ValueError:
        return value


def parse_tel(raw: str) -> dict[str, object]:
    data: dict[str, object] = {}
    if not raw.startswith("TEL,"):
        return data
    for part in raw[4:].split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        if key == "ms":
            key = "esp_ms"
        data[key] = coerce_value(value.strip())
    return data


def send_axis(ser: serial.Serial, abs_y: int, abs_x: int) -> None:
    ser.write(f"AXIS,ABS_Y,{abs_y}\nAXIS,ABS_X,{abs_x}\n".encode("ascii"))
    ser.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Log SLA7078MPRT stepper current sense while commanding ABS_X."
    )
    parser.add_argument("--port", default="/dev/ttyAMA4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output-dir", default="/home/haikan/pipe_robot_logs")
    parser.add_argument("--abs-x", type=int, default=850)
    parser.add_argument("--abs-x-forward", type=int, default=560)
    parser.add_argument("--abs-x-reverse", type=int, default=464)
    parser.add_argument("--abs-y", type=int, default=512)
    parser.add_argument("--warmup-s", type=float, default=3.0)
    parser.add_argument("--run-s", type=float, default=20.0)
    parser.add_argument("--cooldown-s", type=float, default=3.0)
    parser.add_argument("--reciprocate", action="store_true")
    parser.add_argument("--segment-s", type=float, default=2.0)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--command-period-s", type=float, default=0.1)
    args = parser.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / datetime.now().strftime("stepper_current_log_%Y%m%d_%H%M%S.csv")

    stop = False

    def handle_stop(signum, frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    print(f"[LOG] Writing {csv_path}")
    print("[RUN] Ctrl+C to stop.")

    start = time.time()
    next_command = 0.0

    with serial.Serial(args.port, args.baud, timeout=0.05) as ser:
        ser.reset_input_buffer()
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()

            while not stop:
                elapsed = time.time() - start
                if elapsed < args.warmup_s:
                    phase = "warmup_stop"
                    command_x = 512
                elif args.reciprocate:
                    run_elapsed = elapsed - args.warmup_s
                    total_run_s = max(args.segment_s, 0.1) * max(args.cycles, 1) * 2
                    if run_elapsed < total_run_s:
                        segment_index = int(run_elapsed / max(args.segment_s, 0.1))
                        if segment_index % 2 == 0:
                            phase = "step_forward"
                            command_x = args.abs_x_forward
                        else:
                            phase = "step_reverse"
                            command_x = args.abs_x_reverse
                    elif run_elapsed < total_run_s + args.cooldown_s:
                        phase = "cooldown_stop"
                        command_x = 512
                    else:
                        break
                elif elapsed < args.warmup_s + args.run_s:
                    phase = "step_run"
                    command_x = args.abs_x
                elif elapsed < args.warmup_s + args.run_s + args.cooldown_s:
                    phase = "cooldown_stop"
                    command_x = 512
                else:
                    break

                if time.time() >= next_command:
                    send_axis(ser, args.abs_y, command_x)
                    next_command = time.time() + args.command_period_s

                raw = ser.readline().decode("utf-8", errors="replace").strip()
                if not raw:
                    continue

                row = {key: "" for key in FIELDS}
                row.update(
                    {
                        "pc_time": datetime.now().isoformat(timespec="milliseconds"),
                        "elapsed_s": f"{elapsed:.6f}",
                        "phase": phase,
                        "command_abs_y": args.abs_y,
                        "command_abs_x": command_x,
                        "raw": raw,
                    }
                )
                row.update({k: v for k, v in parse_tel(raw).items() if k in row})
                writer.writerow(row)

                if raw.startswith("TEL,"):
                    print(
                        f"[{phase}] step_hz={row['step_hz']} "
                        f"A_v={row['step_sense_a_v']} B_v={row['step_sense_b_v']} "
                        f"A={row['step_sense_a_a']} B={row['step_sense_b_a']}"
                    )

        send_axis(ser, args.abs_y, 512)

    print("[RUN] Finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
