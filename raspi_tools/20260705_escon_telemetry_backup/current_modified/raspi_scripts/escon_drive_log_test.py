from __future__ import annotations

import argparse
import csv
import re
import signal
import threading
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
        data[key.strip()] = coerce_value(value.strip())
    return data


def send_axis(ser: serial.Serial, abs_y: int, abs_x: int) -> None:
    ser.write(f"AXIS,ABS_Y,{abs_y}\nAXIS,ABS_X,{abs_x}\n".encode("ascii"))
    ser.flush()


def reader_loop(
    *,
    ser: serial.Serial,
    writer: csv.DictWriter,
    write_lock: threading.Lock,
    stop_event: threading.Event,
    start_time: float,
    phase_state: dict[str, object],
) -> None:
    while not stop_event.is_set():
        try:
            raw = ser.readline().decode(errors="replace").strip()
        except Exception as exc:
            print(f"[READ] error: {exc}")
            time.sleep(0.2)
            continue

        if not raw:
            continue

        now = time.time()
        parsed = parse_tel(raw)
        row = {field: "" for field in FIELDS}
        row.update(
            {
                "pc_time": datetime.now().isoformat(timespec="milliseconds"),
                "elapsed_s": f"{now - start_time:.6f}",
                "phase": phase_state.get("phase", ""),
                "command_abs_y": phase_state.get("abs_y", ""),
                "command_abs_x": phase_state.get("abs_x", ""),
                "raw": raw,
            }
        )
        for key, value in parsed.items():
            if key in row:
                row[key] = value

        with write_lock:
            writer.writerow(row)

        if raw.startswith("TEL,"):
            print(
                f"[{row['phase']}] y={row['command_abs_y']} "
                f"duty={row['duty']} enabled={row['enabled']} "
                f"rpm={row['rpm']} encoder_rpm={row['encoder_rpm']} "
                f"current_a={row['current_a']} current_v={row['current_v']} "
                f"speed_v={row['speed_v']} state={row['state']}"
            )
        else:
            print(f"[RAW] {raw}")


def run_phase(
    *,
    ser: serial.Serial,
    stop_event: threading.Event,
    phase_state: dict[str, object],
    name: str,
    abs_y: int,
    abs_x: int,
    duration_s: float,
    command_interval_s: float,
) -> None:
    print(f"[PHASE] {name}: ABS_Y={abs_y}, {duration_s:.1f}s")
    phase_state.update({"phase": name, "abs_y": abs_y, "abs_x": abs_x})
    end_time = time.time() + duration_s
    while not stop_event.is_set() and time.time() < end_time:
        send_axis(ser, abs_y, abs_x)
        time.sleep(command_interval_s)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drive ESCON motor from Raspberry Pi while logging ESP telemetry."
    )
    parser.add_argument("--port", default="/dev/ttyAMA4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output-dir", default="/home/haikan/pipe_robot_logs")
    parser.add_argument("--center", type=int, default=512)
    parser.add_argument("--forward-y", type=int, default=760)
    parser.add_argument("--reverse-y", type=int, default=264)
    parser.add_argument("--abs-x", type=int, default=512)
    parser.add_argument("--run-s", type=float, default=12.0)
    parser.add_argument("--stop-s", type=float, default=3.0)
    parser.add_argument("--command-interval-s", type=float, default=0.05)
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / datetime.now().strftime("escon_drive_log_%Y%m%d_%H%M%S.csv")

    stop_event = threading.Event()

    def handle_stop(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    print("[CHECK] ESCON settings recommended before this test:")
    print("  - Analog Monitor 1 = Actual current or Current actual value")
    print("  - Monitor output range must be 0..3.3 V into ESP32 ADC")
    print("  - If reverse current is negative voltage, ESP32 ADC will read 0 V")
    print("[SAFETY] Wheels/motor will rotate after Enter. Keep hands clear.")
    print(f"[CSV] {csv_path}")

    if not args.no_wait:
        input("Press Enter to start the forward/stop/reverse test...")

    start_time = time.time()
    phase_state: dict[str, object] = {
        "phase": "init",
        "abs_y": args.center,
        "abs_x": args.abs_x,
    }
    write_lock = threading.Lock()

    ser = serial.Serial(args.port, args.baud, timeout=0.2)
    print(f"[SERIAL] opened {args.port} @ {args.baud}")

    try:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            reader = threading.Thread(
                target=reader_loop,
                kwargs={
                    "ser": ser,
                    "writer": writer,
                    "write_lock": write_lock,
                    "stop_event": stop_event,
                    "start_time": start_time,
                    "phase_state": phase_state,
                },
                daemon=True,
            )
            reader.start()

            run_phase(
                ser=ser,
                stop_event=stop_event,
                phase_state=phase_state,
                name="stop_before",
                abs_y=args.center,
                abs_x=args.abs_x,
                duration_s=args.stop_s,
                command_interval_s=args.command_interval_s,
            )
            run_phase(
                ser=ser,
                stop_event=stop_event,
                phase_state=phase_state,
                name="forward",
                abs_y=args.forward_y,
                abs_x=args.abs_x,
                duration_s=args.run_s,
                command_interval_s=args.command_interval_s,
            )
            run_phase(
                ser=ser,
                stop_event=stop_event,
                phase_state=phase_state,
                name="stop_middle",
                abs_y=args.center,
                abs_x=args.abs_x,
                duration_s=args.stop_s,
                command_interval_s=args.command_interval_s,
            )
            run_phase(
                ser=ser,
                stop_event=stop_event,
                phase_state=phase_state,
                name="reverse",
                abs_y=args.reverse_y,
                abs_x=args.abs_x,
                duration_s=args.run_s,
                command_interval_s=args.command_interval_s,
            )
            run_phase(
                ser=ser,
                stop_event=stop_event,
                phase_state=phase_state,
                name="stop_after",
                abs_y=args.center,
                abs_x=args.abs_x,
                duration_s=args.stop_s,
                command_interval_s=args.command_interval_s,
            )

            stop_event.set()
            reader.join(timeout=2.0)
            f.flush()
    finally:
        for _ in range(5):
            try:
                send_axis(ser, args.center, args.abs_x)
            except Exception:
                break
            time.sleep(0.05)
        ser.close()
        print("[SERIAL] closed")
        print(f"[DONE] saved {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
