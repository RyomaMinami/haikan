from __future__ import annotations

import argparse
import csv
import json
import socket
import time
import urllib.request
from urllib.error import URLError
from datetime import datetime
from pathlib import Path


FIELDS = [
    "pc_time",
    "elapsed_s",
    "phase",
    "command_abs_x",
    "command_abs_y",
    "state",
    "duty",
    "enabled",
    "step_hz",
    "step_sense_a_v",
    "step_sense_b_v",
    "step_sense_a_a",
    "step_sense_b_a",
    "step_current_abs_a",
    "raw_json",
]


def send_lines(host: str, port: int, lines: list[str]) -> None:
    payload = json.dumps({"lines": lines}, separators=(",", ":")).encode("utf-8")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload, (host, port))
    finally:
        sock.close()


def read_state(host: str, http_port: int, timeout_s: float) -> dict:
    url = f"http://{host}:{http_port}/api/state"
    with urllib.request.urlopen(url, timeout=timeout_s) as res:
        return json.loads(res.read().decode("utf-8"))


def blank_error_row(*, start: float, phase: str, abs_x: int, abs_y: int, error: str) -> dict[str, object]:
    row = {field: "" for field in FIELDS}
    row.update(
        {
            "pc_time": datetime.now().isoformat(timespec="milliseconds"),
            "elapsed_s": f"{time.time() - start:.6f}",
            "phase": phase,
            "command_abs_x": abs_x,
            "command_abs_y": abs_y,
            "state": "api_error",
            "raw_json": error,
        }
    )
    return row


def motor_row(state: dict, *, start: float, phase: str, abs_x: int, abs_y: int) -> dict[str, object]:
    motor = state.get("motor") or {}
    row = {field: "" for field in FIELDS}
    row.update(
        {
            "pc_time": datetime.now().isoformat(timespec="milliseconds"),
            "elapsed_s": f"{time.time() - start:.6f}",
            "phase": phase,
            "command_abs_x": abs_x,
            "command_abs_y": abs_y,
            "raw_json": json.dumps(motor, ensure_ascii=False, separators=(",", ":")),
        }
    )
    for key in (
        "state",
        "duty",
        "enabled",
        "step_hz",
        "step_sense_a_v",
        "step_sense_b_v",
        "step_sense_a_a",
        "step_sense_b_a",
        "step_current_abs_a",
    ):
        row[key] = motor.get(key, "")
    return row


def command_for_phase(elapsed: float, args: argparse.Namespace) -> tuple[str, int]:
    if elapsed < args.stop_s:
        return "stop_before", 512

    run_elapsed = elapsed - args.stop_s
    total_run_s = args.segment_s * args.cycles * 2
    if run_elapsed < total_run_s:
        segment = int(run_elapsed / args.segment_s)
        if segment % 2 == 0:
            return "step_forward", args.abs_x_forward
        return "step_reverse", args.abs_x_reverse

    if run_elapsed < total_run_s + args.stop_s:
        return "stop_after", 512
    return "done", 512


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tune/check stepper current by commanding small reciprocating motion through dashboard API/UDP."
    )
    parser.add_argument("--pi-host", default="192.168.50.154")
    parser.add_argument("--http-port", type=int, default=8090)
    parser.add_argument("--command-port", type=int, default=8092)
    parser.add_argument("--output", default="stepper_current_tune_api.csv")
    parser.add_argument("--abs-y", type=int, default=512)
    parser.add_argument("--abs-x-forward", type=int, default=560)
    parser.add_argument("--abs-x-reverse", type=int, default=464)
    parser.add_argument("--stop-s", type=float, default=3.0)
    parser.add_argument("--segment-s", type=float, default=1.0)
    parser.add_argument("--cycles", type=int, default=3)
    parser.add_argument("--sample-period-s", type=float, default=0.2)
    parser.add_argument("--command-period-s", type=float, default=0.1)
    parser.add_argument("--timeout-s", type=float, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    csv_path = Path(args.output)
    print(f"[CSV] {csv_path.resolve()}")
    print("[SAFETY] This sends small stepper forward/reverse commands.")
    print("         Keep the mechanism clear. Use Ctrl+C to stop.")
    if args.dry_run:
        print("[DRY] No commands will be sent.")

    start = time.time()
    next_command = 0.0
    next_sample = 0.0
    current_phase = ""
    current_abs_x = 512

    try:
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()

            while True:
                elapsed = time.time() - start
                phase, abs_x = command_for_phase(elapsed, args)
                if phase == "done":
                    break

                if phase != current_phase:
                    print(f"[PHASE] {phase}: ABS_X={abs_x}")
                    current_phase = phase
                    current_abs_x = abs_x

                now = time.time()
                if now >= next_command:
                    if not args.dry_run:
                        send_lines(
                            args.pi_host,
                            args.command_port,
                            [f"AXIS,ABS_X,{abs_x}", f"AXIS,ABS_Y,{args.abs_y}"],
                        )
                    next_command = now + args.command_period_s

                if now >= next_sample:
                    try:
                        state = read_state(args.pi_host, args.http_port, args.timeout_s)
                        row = motor_row(
                            state,
                            start=start,
                            phase=phase,
                            abs_x=current_abs_x,
                            abs_y=args.abs_y,
                        )
                    except (TimeoutError, URLError, OSError) as exc:
                        row = blank_error_row(
                            start=start,
                            phase=phase,
                            abs_x=current_abs_x,
                            abs_y=args.abs_y,
                            error=str(exc),
                        )
                    writer.writerow(row)
                    f.flush()
                    if row["state"] == "api_error":
                        print(f"[{phase}] API error: {row['raw_json']}")
                    else:
                        print(
                            f"[{phase}] step_hz={row['step_hz']} "
                            f"A_v={row['step_sense_a_v']} B_v={row['step_sense_b_v']} "
                            f"A={row['step_sense_a_a']} B={row['step_sense_b_a']}"
                        )
                    next_sample = now + args.sample_period_s

                time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C")
    finally:
        if not args.dry_run:
            for _ in range(5):
                send_lines(args.pi_host, args.command_port, ["AXIS,ABS_X,512", "AXIS,ABS_Y,512"])
                time.sleep(0.05)
        print("[DONE] neutral command sent.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
