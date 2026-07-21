from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


DEFAULT_PI_HOST = "192.168.0.218"
DEFAULT_DASHBOARD_PORT = 8090
DEFAULT_COMMAND_PORT = 8092
DEFAULT_SSH_USER = "haikan"
DEFAULT_SSH_KEY = Path.home() / "yes"


def send_lines(pi_host: str, command_port: int, lines: list[str], repeat: int = 1) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        payload = json.dumps({"lines": lines}, separators=(",", ":")).encode("utf-8")
        for _ in range(max(1, repeat)):
            sock.sendto(payload, (pi_host, command_port))
            time.sleep(0.05)
    finally:
        sock.close()


def read_state(pi_host: str, dashboard_port: int, timeout_s: float = 2.0) -> dict:
    url = f"http://{pi_host}:{dashboard_port}/api/state"
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def stop_controller_sender() -> None:
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine -match 'pc_valve_controller_sender.py|pc_experiment_launcher.py' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_robot(args: argparse.Namespace) -> None:
    lines = [
        "AXIS,ABS_X,512",
        "AXIS,ABS_Y,512",
        "VALVE,ALL,0",
    ]
    print("[SEND] stop robot operation: neutral axes and all valves off")
    send_lines(args.pi_host, args.command_port, lines, repeat=args.repeat)
    if args.stop_pc_sender:
        print("[STOP] local PC controller sender")
        stop_controller_sender()


def restart_pi_system(args: argparse.Namespace) -> None:
    key = Path(args.ssh_key).expanduser()
    if not key.exists():
        raise FileNotFoundError(f"SSH key not found: {key}")

    remote = (
        "cd /home/haikan/pipe_robot_dev/production && "
        "./stop_production_robot.sh || true; "
        "sleep 1; "
        "./start_production_robot.sh"
    )
    command = [
        "ssh",
        "-i",
        str(key),
        "-o",
        "ConnectTimeout=6",
        "-o",
        "StrictHostKeyChecking=no",
        f"{args.ssh_user}@{args.pi_host}",
        remote,
    ]
    print("[SSH] restart Pi production system")
    subprocess.run(command, check=True)


def print_status(args: argparse.Namespace) -> None:
    state = read_state(args.pi_host, args.dashboard_port)
    controller = state.get("controller") if isinstance(state.get("controller"), dict) else {}
    motor = state.get("motor") if isinstance(state.get("motor"), dict) else {}
    valves = state.get("valves") if isinstance(state.get("valves"), dict) else {}
    serial = state.get("serial") if isinstance(state.get("serial"), dict) else {}

    print("[STATUS]")
    print(f"  controller: {controller.get('name', '-')} axes={controller.get('axes', '-')} buttons={controller.get('pressed_buttons', [])}")
    print(
        "  motor     : "
        f"state={motor.get('state', '-')} duty={motor.get('duty', '-')} "
        f"step_hz={motor.get('step_hz', '-')} encoder_rpm={motor.get('encoder_rpm', '-')}"
    )
    print(f"  valves    : {valves}")
    print(f"  serial    : {serial}")


def main() -> int:
    parser = argparse.ArgumentParser(description="PC-side command helper for the pipe robot.")
    parser.add_argument("command", choices=["status", "stop", "restart-pi-system"])
    parser.add_argument("--pi-host", default=DEFAULT_PI_HOST)
    parser.add_argument("--dashboard-port", type=int, default=DEFAULT_DASHBOARD_PORT)
    parser.add_argument("--command-port", type=int, default=DEFAULT_COMMAND_PORT)
    parser.add_argument("--repeat", type=int, default=5)
    parser.add_argument("--stop-pc-sender", action="store_true")
    parser.add_argument("--ssh-user", default=DEFAULT_SSH_USER)
    parser.add_argument("--ssh-key", default=str(DEFAULT_SSH_KEY))
    args = parser.parse_args()

    try:
        if args.command == "status":
            print_status(args)
        elif args.command == "stop":
            stop_robot(args)
        elif args.command == "restart-pi-system":
            restart_pi_system(args)
        else:
            raise AssertionError(args.command)
    except Exception as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
