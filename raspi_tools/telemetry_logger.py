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


ESP1_RE = re.compile(
    r"ACC:([-\d.]+),([-\d.]+),([-\d.]+),GY:(\d),DST:(\d+),PRS:([\d.]+)"
)


def parse_line(raw: str) -> dict[str, object]:
    data: dict[str, object] = {}

    m = ESP1_RE.search(raw)
    if m:
        data.update(
            {
                "ax_g": float(m.group(1)),
                "ay_g": float(m.group(2)),
                "az_g": float(m.group(3)),
                "photo_gate": int(m.group(4)),
                "distance_mm": int(m.group(5)),
                "pressure_mpa": float(m.group(6)),
            }
        )
        return data

    # Future telemetry examples:
    # TEL,ms=1234,id=1,rpm=120.5,current_a=0.34,voltage_v=24.1,state=move
    # TEL,1234,1,120.5,0.34,24.1,move
    if raw.startswith("TEL,"):
        body = raw[4:]
        parts = [p.strip() for p in body.split(",")]
        kv_seen = False
        for part in parts:
            if "=" not in part:
                continue
            kv_seen = True
            key, value = part.split("=", 1)
            key = key.strip()
            value = value.strip()
            data[key] = coerce_value(value)

        if not kv_seen and len(parts) >= 6:
            data.update(
                {
                    "esp_ms": coerce_value(parts[0]),
                    "motor_id": coerce_value(parts[1]),
                    "rpm": coerce_value(parts[2]),
                    "current_a": coerce_value(parts[3]),
                    "voltage_v": coerce_value(parts[4]),
                    "state": parts[5],
                }
            )

    return data


def coerce_value(value: str) -> object:
    try:
        if re.fullmatch(r"[-+]?\d+", value):
            return int(value)
        return float(value)
    except ValueError:
        return value


def reader_thread(
    *,
    name: str,
    port: str,
    baud: int,
    writer: csv.DictWriter,
    write_lock: threading.Lock,
    stop_event: threading.Event,
) -> None:
    try:
        ser = serial.Serial(port, baud, timeout=0.2)
        print(f"[{name}] opened {port} @ {baud}")
    except Exception as exc:
        print(f"[{name}] open failed: {exc}")
        return

    try:
        while not stop_event.is_set():
            try:
                raw = ser.readline().decode(errors="replace").strip()
            except Exception as exc:
                print(f"[{name}] read error: {exc}")
                time.sleep(0.2)
                continue

            if not raw:
                continue

            now = time.time()
            row = {
                "pc_time": datetime.now().isoformat(timespec="milliseconds"),
                "elapsed_s": f"{now - START_TIME:.6f}",
                "source": name,
                "port": port,
                "raw": raw,
                "ax_g": "",
                "ay_g": "",
                "az_g": "",
                "photo_gate": "",
                "distance_mm": "",
                "pressure_mpa": "",
                "esp_ms": "",
                "motor_id": "",
                "rpm": "",
                "current_a": "",
                "current_v": "",
                "speed_v": "",
                "encoder_rpm": "",
                "adc27_v": "",
                "adc32_v": "",
                "adc33_v": "",
                "adc34_v": "",
                "adc35_v": "",
                "adc36_v": "",
                "adc39_v": "",
                "step_sense_a_v": "",
                "step_sense_b_v": "",
                "step_sense_a_a": "",
                "step_sense_b_a": "",
                "step_current_abs_a": "",
                "voltage_v": "",
                "duty": "",
                "enabled": "",
                "step_hz": "",
                "estop": "",
                "state": "",
            }
            parsed = parse_line(raw)
            for key, value in parsed.items():
                if key in row:
                    row[key] = value

            with write_lock:
                writer.writerow(row)
            print(f"[{name}] {raw}")
    finally:
        ser.close()
        print(f"[{name}] closed {port}")


START_TIME = time.time()


def main() -> int:
    parser = argparse.ArgumentParser(description="ESP telemetry raw/CSV logger")
    parser.add_argument("--esp1-port", default="/dev/ttyAMA2")
    parser.add_argument("--esp2-port", default="/dev/ttyAMA4")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--output-dir", default="/home/haikan/pipe_robot_logs")
    parser.add_argument("--no-esp1", action="store_true")
    parser.add_argument("--no-esp2", action="store_true")
    args = parser.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    csv_path = outdir / datetime.now().strftime("telemetry_%Y%m%d_%H%M%S.csv")

    fields = [
        "pc_time",
        "elapsed_s",
        "source",
        "port",
        "raw",
        "ax_g",
        "ay_g",
        "az_g",
        "photo_gate",
        "distance_mm",
        "pressure_mpa",
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

    stop_event = threading.Event()
    write_lock = threading.Lock()

    def handle_stop(signum, frame):
        stop_event.set()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()

        threads: list[threading.Thread] = []
        if not args.no_esp1:
            threads.append(
                threading.Thread(
                    target=reader_thread,
                    kwargs={
                        "name": "ESP1",
                        "port": args.esp1_port,
                        "baud": args.baud,
                        "writer": writer,
                        "write_lock": write_lock,
                        "stop_event": stop_event,
                    },
                    daemon=True,
                )
            )
        if not args.no_esp2:
            threads.append(
                threading.Thread(
                    target=reader_thread,
                    kwargs={
                        "name": "ESP2",
                        "port": args.esp2_port,
                        "baud": args.baud,
                        "writer": writer,
                        "write_lock": write_lock,
                        "stop_event": stop_event,
                    },
                    daemon=True,
                )
            )

        for thread in threads:
            thread.start()

        print(f"[LOG] writing {csv_path}")
        print("[RUN] Ctrl+C to stop")
        try:
            while not stop_event.is_set():
                f.flush()
                time.sleep(0.5)
        finally:
            stop_event.set()
            for thread in threads:
                thread.join(timeout=2.0)
            f.flush()
            print(f"[DONE] saved {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
