from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


DEFAULT_PI_HOST = "192.168.0.218"
DEFAULT_DASHBOARD_PORT = 8090
DEFAULT_COMMAND_PORT = 8092
DEFAULT_CONTROLLER_PORT = 8091


def read_api(pi_host: str, dashboard_port: int, timeout_s: float = 1.0) -> dict:
    url = f"http://{pi_host}:{dashboard_port}/api/state"
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def wait_for_pi(pi_host: str, dashboard_port: int, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            state = read_api(pi_host, dashboard_port, timeout_s=1.0)
            print(f"[OK] Pi dashboard API connected: http://{pi_host}:{dashboard_port}/api/state")
            return state
        except Exception as exc:
            last_error = exc
            print(f"[WAIT] Pi API not ready: {exc}")
            time.sleep(1.0)
    raise RuntimeError(f"Pi API did not become ready within {timeout_s:g}s: {last_error}")


def send_lines(pi_host: str, command_port: int, lines: list[str]) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        payload = json.dumps({"lines": lines}, separators=(",", ":")).encode("utf-8")
        sock.sendto(payload, (pi_host, command_port))
    finally:
        sock.close()


def open_dashboard(pi_host: str, dashboard_port: int) -> None:
    url = f"http://{pi_host}:{dashboard_port}/robot_dashboard.html"
    print(f"[OPEN] Dashboard: {url}")
    webbrowser.open(url)


def start_controller_sender(args: argparse.Namespace, script_dir: Path) -> subprocess.Popen:
    sender = script_dir / "pc_valve_controller_sender.py"
    log_path = script_dir / "pc_experiment_controller_sender.log"
    err_path = script_dir / "pc_experiment_controller_sender.err"
    command = [
        sys.executable,
        str(sender),
        "--pi-host",
        args.pi_host,
        "--command-port",
        str(args.command_port),
        "--controller-port",
        str(args.controller_port),
        "--deadzone",
        str(args.deadzone),
        "--step-axis",
        str(args.step_axis),
        "--motor-axis",
        str(args.motor_axis),
    ]
    for item in args.map:
        command.extend(["--map", item])
    if args.grinder_on_button is not None:
        command.extend(["--grinder-on-button", str(args.grinder_on_button)])
    if args.grinder_off_button is not None:
        command.extend(["--grinder-off-button", str(args.grinder_off_button)])
    if args.invert_step:
        command.append("--invert-step")
    if args.no_invert_motor:
        command.append("--no-invert-motor")

    print("[START] PC controller sender")
    print(f"        log: {log_path}")
    stdout = log_path.open("w", encoding="utf-8", errors="replace")
    stderr = err_path.open("w", encoding="utf-8", errors="replace")
    return subprocess.Popen(command, stdout=stdout, stderr=stderr)


def format_age(state: dict, key: str) -> str:
    item = state.get(key)
    if not isinstance(item, dict):
        return "-"
    updated = item.get("updated_s")
    if not isinstance(updated, (int, float)):
        return "-"
    return f"{max(0.0, time.time() - float(updated)):.1f}s"


def summarize_state(state: dict) -> str:
    controller = state.get("controller") if isinstance(state.get("controller"), dict) else {}
    motor = state.get("motor") if isinstance(state.get("motor"), dict) else {}
    valves = state.get("valves") if isinstance(state.get("valves"), dict) else {}
    serial = state.get("serial") if isinstance(state.get("serial"), dict) else {}

    axes = controller.get("axes", "-")
    pressed = controller.get("pressed_buttons", [])
    valve_on = []
    if isinstance(valves, dict):
        for name, value in valves.items():
            if value:
                valve_on.append(name)

    esp = []
    if isinstance(serial, dict):
        for name in ("esp1", "esp2"):
            data = serial.get(name)
            if isinstance(data, dict):
                esp.append(f"{name}:{data.get('status', '-')}")

    return (
        f"controller age={format_age(state, 'controller')} axes={axes} buttons={pressed} | "
        f"motor state={motor.get('state', '-')} duty={motor.get('duty', '-')} "
        f"step_hz={motor.get('step_hz', '-')} rpm={motor.get('encoder_rpm', motor.get('rpm', '-'))} | "
        f"valves={valve_on or ['-']} | serial={','.join(esp) or '-'}"
    )


def monitor(args: argparse.Namespace, controller_proc: subprocess.Popen | None) -> None:
    last_line = ""
    warn_controller_after_s = 3.0
    while True:
        if controller_proc is not None and controller_proc.poll() is not None:
            print(f"[ERROR] Controller sender stopped: returncode={controller_proc.returncode}")
            break

        try:
            state = read_api(args.pi_host, args.dashboard_port, timeout_s=1.0)
            line = summarize_state(state)
            if line != last_line:
                print(f"[STATE] {line}")
                last_line = line

            age_text = format_age(state, "controller")
            try:
                age = float(age_text.removesuffix("s"))
                if age > warn_controller_after_s:
                    print("[WARN] Controller state is old. Check PC sender or controller connection.")
            except ValueError:
                pass
        except Exception as exc:
            print(f"[WARN] Failed to read Pi state: {exc}")

        time.sleep(args.monitor_interval)


def stop_safely(pi_host: str, command_port: int, controller_proc: subprocess.Popen | None) -> None:
    print("[STOP] Sending neutral/off command.")
    for _ in range(3):
        try:
            send_lines(pi_host, command_port, ["AXIS,ABS_X,512", "AXIS,ABS_Y,512", "VALVE,ALL,0"])
        except Exception as exc:
            print(f"[WARN] Stop command failed: {exc}")
        time.sleep(0.05)

    if controller_proc is not None and controller_proc.poll() is None:
        controller_proc.terminate()
        try:
            controller_proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            controller_proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="PC-side launcher for pipe robot dashboard and controller operation."
    )
    parser.add_argument("--pi-host", default=DEFAULT_PI_HOST)
    parser.add_argument("--dashboard-port", type=int, default=DEFAULT_DASHBOARD_PORT)
    parser.add_argument("--command-port", type=int, default=DEFAULT_COMMAND_PORT)
    parser.add_argument("--controller-port", type=int, default=DEFAULT_CONTROLLER_PORT)
    parser.add_argument("--wait-timeout-s", type=float, default=30.0)
    parser.add_argument("--monitor-interval", type=float, default=1.0)
    parser.add_argument("--deadzone", type=float, default=0.18)
    parser.add_argument("--step-axis", type=int, default=0)
    parser.add_argument("--motor-axis", type=int, default=1)
    parser.add_argument("--invert-step", action="store_true")
    parser.add_argument("--no-invert-motor", action="store_true")
    parser.add_argument("--map", action="append", default=[])
    parser.add_argument("--grinder-on-button", type=int, default=10)
    parser.add_argument("--grinder-off-button", type=int, default=11)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-controller", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    controller_proc: subprocess.Popen | None = None
    try:
        state = wait_for_pi(args.pi_host, args.dashboard_port, args.wait_timeout_s)
        print(f"[PI] {summarize_state(state)}")
        send_lines(args.pi_host, args.command_port, ["PC_HELLO"])
        print("[SEND] PC_HELLO")

        if not args.no_browser:
            open_dashboard(args.pi_host, args.dashboard_port)
        if not args.no_controller:
            controller_proc = start_controller_sender(args, script_dir)

        print("[RUN] Ctrl+Cで停止します。停止時はモータ中立、電磁弁OFFを送ります。")
        monitor(args, controller_proc)
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C")
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    finally:
        stop_safely(args.pi_host, args.command_port, controller_proc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
