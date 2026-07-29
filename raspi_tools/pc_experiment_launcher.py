from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import quote, urlparse


DEFAULT_PI_HOST = "192.168.0.218"
DEFAULT_DASHBOARD_PORT = 8090
DEFAULT_LOCAL_DASHBOARD_PORT = 8095
DEFAULT_COMMAND_PORT = 8092
DEFAULT_CONTROLLER_PORT = 8091


def validate_model_config(config: object) -> dict:
    if not isinstance(config, dict) or config.get("version") not in {1, 2}:
        raise ValueError("model config version must be 1 or 2")
    modules = config.get("modules", [])
    if not isinstance(modules, list):
        raise ValueError("modules must be an array")
    module_ids: set[int] = set()
    for module in modules:
        if not isinstance(module, dict):
            raise ValueError("each module must be an object")
        module_id = module.get("id")
        if not isinstance(module_id, int) or module_id < 0:
            raise ValueError("each module requires a non-negative integer id")
        if module_id in module_ids:
            raise ValueError(f"duplicate module id: {module_id}")
        if not isinstance(module.get("name"), str) or not module["name"]:
            raise ValueError(f"module requires a name: {module_id}")
        if not isinstance(module.get("file"), str) or not module["file"]:
            raise ValueError(f"module requires a file: {module_id}")
        module_ids.add(module_id)
    joints = config.get("joints")
    if not isinstance(joints, list):
        raise ValueError("joints must be an array")
    by_id: dict[str, dict] = {}
    for joint in joints:
        if not isinstance(joint, dict):
            raise ValueError("each joint must be an object")
        joint_id = joint.get("id")
        if not isinstance(joint_id, str) or not joint_id:
            raise ValueError("each joint requires a non-empty id")
        if joint_id in by_id:
            raise ValueError(f"duplicate joint id: {joint_id}")
        if joint.get("type") not in {"revolute", "prismatic"}:
            raise ValueError(f"invalid joint type: {joint_id}")
        axis = joint.get("axis")
        pivot = joint.get("pivot")
        if not isinstance(axis, list) or len(axis) != 3:
            raise ValueError(f"joint axis must have three values: {joint_id}")
        if not isinstance(pivot, list) or len(pivot) != 3:
            raise ValueError(f"joint pivot must have three values: {joint_id}")
        if not isinstance(joint.get("components"), list):
            raise ValueError(f"joint components must be an array: {joint_id}")
        by_id[joint_id] = joint
    for joint_id, joint in by_id.items():
        parent = joint.get("parent")
        if parent is not None and parent not in by_id:
            raise ValueError(f"unknown parent {parent!r} for {joint_id}")
        visited = {joint_id}
        while parent is not None:
            if parent in visited:
                raise ValueError(f"joint parent cycle detected at {joint_id}")
            visited.add(parent)
            parent = by_id[parent].get("parent")
    return config


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


class LocalDashboardHandler(SimpleHTTPRequestHandler):
    """Serve PC-local dashboard assets and proxy only the Pi state API."""

    def __init__(
        self,
        *args,
        pi_api_url: str,
        model_config_path: Path,
        **kwargs,
    ) -> None:
        self.pi_api_url = pi_api_url
        self.model_config_path = model_config_path
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            self.proxy_state_api()
            return
        if path == "/api/model-config":
            self.send_model_config()
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/model-config":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 2_000_000:
                raise ValueError("invalid config size")
            config = json.loads(self.rfile.read(length).decode("utf-8"))
            config = validate_model_config(config)
            body = json.dumps(
                config,
                ensure_ascii=False,
                indent=2,
                sort_keys=False,
            ).encode("utf-8")
            temporary = self.model_config_path.with_suffix(".json.tmp")
            temporary.write_bytes(body)
            temporary.replace(self.model_config_path)
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps(
                {"error": "invalid model config", "detail": str(exc)},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def send_model_config(self) -> None:
        try:
            body = self.model_config_path.read_bytes()
            json.loads(body.decode("utf-8"))
            status = 200
        except FileNotFoundError:
            body = b'{"version":1,"joints":[]}'
            status = 200
        except Exception as exc:
            body = json.dumps(
                {"error": "model config unavailable", "detail": str(exc)},
                ensure_ascii=False,
            ).encode("utf-8")
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def proxy_state_api(self) -> None:
        try:
            request = urllib.request.Request(
                self.pi_api_url,
                headers={"Accept": "application/json", "Cache-Control": "no-cache"},
            )
            with urllib.request.urlopen(request, timeout=1.5) as response:
                body = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "application/json")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as exc:
            body = json.dumps(
                {"error": "Pi state API unavailable", "detail": str(exc)},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    def end_headers(self) -> None:
        if urlparse(self.path).path.endswith((".html", ".js")):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        if urlparse(self.path).path != "/api/state":
            super().log_message(format, *args)


def start_local_dashboard(
    pi_host: str,
    pi_dashboard_port: int,
    local_port: int,
    dashboard_dir: Path,
) -> tuple[ThreadingHTTPServer, threading.Thread]:
    pi_api_url = f"http://{pi_host}:{pi_dashboard_port}/api/state"
    handler = partial(
        LocalDashboardHandler,
        directory=str(dashboard_dir),
        pi_api_url=pi_api_url,
        model_config_path=dashboard_dir / "robot_model_config.json",
    )
    server = ThreadingHTTPServer(("127.0.0.1", local_port), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name="pc-local-dashboard",
        daemon=True,
    )
    thread.start()
    print(f"[START] PC-local dashboard: http://127.0.0.1:{local_port}")
    print(f"        Pi state proxy: {pi_api_url}")
    print(
        f"        Joint editor: http://127.0.0.1:{local_port}/"
        "robot_model_joint_editor.html"
    )
    return server, thread


def open_dashboard(pi_host: str, local_port: int, use_cad: bool = True) -> None:
    cad_query = "&cad=1" if use_cad else ""
    url = (
        f"http://127.0.0.1:{local_port}/robot_dashboard.html"
        f"?pi={quote(pi_host, safe='')}{cad_query}"
    )
    print(f"[OPEN] PC-local dashboard: {url}")
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
        "--dashboard-port",
        str(args.dashboard_port),
        "--deadzone",
        str(args.deadzone),
        "--step-axis",
        str(args.step_axis),
        "--motor-axis",
        str(args.motor_axis),
        "--motor-response-exponent",
        str(args.motor_response_exponent),
        "--motor-throttle-full-scale",
        str(args.motor_throttle_full_scale),
        "--motor-max-duty-ratio",
        str(args.motor_max_duty_ratio),
        "--motor-full-speed-hat",
        str(args.motor_full_speed_hat),
        "--pressure-target-mpa",
        str(args.pressure_target_mpa),
        "--pressure-hysteresis-mpa",
        str(args.pressure_hysteresis_mpa),
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
    if args.motor_unipolar:
        command.append("--motor-unipolar")
    if args.motor_throttle_axis is not None:
        command.extend(["--motor-throttle-axis", str(args.motor_throttle_axis)])
    if args.invert_motor_throttle:
        command.append("--invert-motor-throttle")
    if args.roll_position_change_mode:
        command.append("--pressure-control")

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
            if name in {"last_raw", "updated_s", "mask"}:
                continue
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
    parser.add_argument(
        "--local-dashboard-port",
        type=int,
        default=DEFAULT_LOCAL_DASHBOARD_PORT,
    )
    parser.add_argument("--command-port", type=int, default=DEFAULT_COMMAND_PORT)
    parser.add_argument("--controller-port", type=int, default=DEFAULT_CONTROLLER_PORT)
    parser.add_argument("--wait-timeout-s", type=float, default=30.0)
    parser.add_argument(
        "--require-pi",
        action="store_true",
        help="Wait for the Raspberry Pi API before starting controller operation.",
    )
    parser.add_argument("--monitor-interval", type=float, default=1.0)
    parser.add_argument("--deadzone", type=float, default=0.18)
    parser.add_argument("--step-axis", type=int, default=0)
    parser.add_argument("--motor-axis", type=int, default=1)
    parser.add_argument("--motor-response-exponent", type=float, default=1.35)
    parser.add_argument("--motor-unipolar", action="store_true")
    parser.add_argument("--motor-throttle-axis", type=int)
    parser.add_argument("--motor-throttle-full-scale", type=float, default=0.95)
    parser.add_argument("--invert-motor-throttle", action="store_true")
    parser.add_argument("--motor-max-duty-ratio", type=float, default=1.0)
    parser.add_argument("--motor-full-speed-hat", type=int, default=0)
    parser.add_argument("--invert-step", action="store_true")
    parser.add_argument("--no-invert-motor", action="store_true")
    parser.add_argument("--map", action="append", default=[])
    parser.add_argument("--grinder-on-button", type=int, default=10)
    parser.add_argument("--grinder-off-button", type=int, default=11)
    parser.add_argument("--pressure-target-mpa", type=float, default=0.60)
    parser.add_argument("--pressure-hysteresis-mpa", type=float, default=0.02)
    parser.add_argument(
        "--roll-position-change-mode",
        action="store_true",
        help="Enable the 0.6 MPa DRILL_PUSH regulator for roll-position adjustment.",
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-controller", action="store_true")
    parser.add_argument(
        "--lightweight-model",
        action="store_true",
        help="Use the procedural model instead of the configured local CAD model.",
    )
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    dashboard_dir = script_dir / "camera_stream"
    controller_proc: subprocess.Popen | None = None
    local_dashboard: ThreadingHTTPServer | None = None
    local_dashboard_thread: threading.Thread | None = None
    try:
        local_dashboard, local_dashboard_thread = start_local_dashboard(
            args.pi_host,
            args.dashboard_port,
            args.local_dashboard_port,
            dashboard_dir,
        )
        if not args.no_browser:
            open_dashboard(
                args.pi_host,
                args.local_dashboard_port,
                use_cad=not args.lightweight_model,
            )

        pi_connected = False
        try:
            if args.require_pi:
                state = wait_for_pi(args.pi_host, args.dashboard_port, args.wait_timeout_s)
            else:
                state = read_api(args.pi_host, args.dashboard_port, timeout_s=0.5)
            pi_connected = True
            print(f"[PI] {summarize_state(state)}")
        except Exception as exc:
            if args.require_pi:
                raise
            print(f"[OFFLINE] Pi API is not available; PC dashboard will continue: {exc}")

        if pi_connected:
            send_lines(args.pi_host, args.command_port, ["PC_HELLO"])
            print("[SEND] PC_HELLO")
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
        if local_dashboard is not None:
            print("[STOP] PC-local dashboard")
            local_dashboard.shutdown()
            local_dashboard.server_close()
        if local_dashboard_thread is not None:
            local_dashboard_thread.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
