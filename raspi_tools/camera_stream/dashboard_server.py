#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import re
import socket
import threading
import time
from collections import deque
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    import serial
except Exception:  # pragma: no cover - dashboard can still serve static pages
    serial = None

try:
    import lgpio
except Exception:  # pragma: no cover - optional Raspberry Pi GPIO support
    lgpio = None


ESP1_RE = re.compile(
    r"ACC:([-\d.]+),([-\d.]+),([-\d.]+),GY:(\d),DST:(\d+),PRS:([\d.]+)"
)
KEY_VALUE_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):?=([-\d.]+)")
GRINDER_PULSES_PER_REV = 24
GRINDER_EDGE_WINDOW_S = 1.0
BODY_ACCEL_PINS = {"x": 24, "y": 23, "z": 27}
BODY_ACCEL_OFFSET_MM = {"x": 0.0, "y": -8.517, "z": -(65.34 + 16.0)}
ACCE1_ADC_PINS = {"x": 33, "y": 25, "z": 26}
VALVE_GPIO_PINS = {
    "DRILL_PUSH": 17,
    "DRILL_PULL": 18,
    "MOVE_PULL": 19,
    "MOVE_PUSH": 20,
    "GRINDER_AIR": 22,
}
VALVE_GPIO_CHIP_CANDIDATES = (4, 0, 1, 2, 3)
VALVE_STATE_KEYS = {
    "MOVE_PUSH": "move_push",
    "MOVE_PULL": "move_pull",
    "DRILL_PUSH": "drill_push",
    "DRILL_PULL": "drill_pull",
    "GRINDER_AIR": "grinder_air",
}
VALVE_INTERLOCKS = {
    "MOVE_PUSH": "MOVE_PULL",
    "MOVE_PULL": "MOVE_PUSH",
    "DRILL_PUSH": "DRILL_PULL",
    "DRILL_PULL": "DRILL_PUSH",
}
VALVE_ACTIVE_HIGH = True
VALVE_HOLD_TIMEOUT_S = 0.35


STATE: dict[str, Any] = {
    "server_time": "",
    "imu": {
        "ax_g": None,
        "ay_g": None,
        "az_g": None,
        "roll_deg": 0.0,
        "pitch_deg": 0.0,
        "yaw_deg": 0.0,
        "last_raw": "",
        "updated_s": None,
    },
    "sensors": {
        "photo_gate": None,
        "ki1233_signal": None,
        "ki1233_pulse_hz": None,
        "grinder_rpm": None,
        "grinder_rpm_source": "",
        "distance_mm": None,
        "pressure_mpa": None,
    },
    "motor": {
        "state": "",
        "duty": None,
        "enabled": None,
        "rpm": None,
        "encoder_rpm": None,
        "current_a": None,
        "current_v": None,
        "step_hz": None,
        "step_sense_a_v": None,
        "step_sense_b_v": None,
        "step_sense_a_a": None,
        "step_sense_b_a": None,
        "step_current_abs_a": None,
        "last_raw": "",
        "updated_s": None,
    },
    "valves": {
        "move_push": 0,
        "move_pull": 0,
        "drill_push": 0,
        "drill_pull": 0,
        "grinder_air": 0,
        "mask": 0,
        "last_raw": "",
        "updated_s": None,
    },
    "controller": {
        "name": "",
        "source_ip": "",
        "seq": None,
        "axes": [],
        "buttons": [],
        "pressed_buttons": [],
        "hats": [],
        "last_raw": "",
        "updated_s": None,
    },
    "body_accel": {
        "pins": BODY_ACCEL_PINS,
        "mount_offset_mm": BODY_ACCEL_OFFSET_MM,
        "raw_gpio": {"x": None, "y": None, "z": None},
        "raw_voltage_v": {"x": None, "y": None, "z": None},
        "ax_g": None,
        "ay_g": None,
        "az_g": None,
        "roll_deg": None,
        "pitch_deg": None,
        "status": "not started",
        "note": "Raspberry Pi GPIO is digital-only; AE-KXR94 analog tilt needs an ADC.",
        "updated_s": None,
    },
    "ports": {},
}
LOCK = threading.Lock()
SERIAL_LOCK = threading.Lock()
VALVE_LOCK = threading.Lock()
SERIAL_PORTS: dict[str, Any] = {}
VALVE_CHIP: Any | None = None
VALVE_LAST_ON: dict[str, float] = {name: 0.0 for name in VALVE_GPIO_PINS}
VALVE_ON: dict[str, bool] = {name: False for name in VALVE_GPIO_PINS}
GRINDER_EDGE_TIMES: deque[float] = deque()
GRINDER_LAST_SIGNAL: int | None = None


def estimate_grinder_from_signal(signal: int, now: float) -> tuple[float | None, float | None]:
    """Estimate grinder speed from KI1233-AA rising edges when only a binary signal is available."""
    global GRINDER_LAST_SIGNAL

    if GRINDER_LAST_SIGNAL is not None and GRINDER_LAST_SIGNAL == 0 and signal == 1:
        GRINDER_EDGE_TIMES.append(now)
    GRINDER_LAST_SIGNAL = signal

    while GRINDER_EDGE_TIMES and now - GRINDER_EDGE_TIMES[0] > GRINDER_EDGE_WINDOW_S:
        GRINDER_EDGE_TIMES.popleft()

    if len(GRINDER_EDGE_TIMES) < 2:
        return None, None

    duration = GRINDER_EDGE_TIMES[-1] - GRINDER_EDGE_TIMES[0]
    if duration <= 0:
        return None, None

    pulse_hz = (len(GRINDER_EDGE_TIMES) - 1) / duration
    rpm = pulse_hz * 60.0 / GRINDER_PULSES_PER_REV
    return pulse_hz, rpm


def parse_extra_key_values(raw: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key, value in KEY_VALUE_RE.findall(raw):
        values[key.lower()] = coerce_value(value)
    return values


def coerce_value(value: str) -> Any:
    try:
        if re.fullmatch(r"[-+]?\d+", value):
            return int(value)
        return float(value)
    except ValueError:
        return value


def accel_to_orientation(ax: float, ay: float, az: float) -> tuple[float, float]:
    roll = math.degrees(math.atan2(ay, az))
    pitch = math.degrees(math.atan2(-ax, math.sqrt(ay * ay + az * az)))
    return roll, pitch


def build_body_accel_from_esp2(data: dict[str, Any], now: float) -> dict[str, Any] | None:
    try:
        vx = float(data["acce1_x_v"])
        vy = float(data["acce1_y_v"])
        vz = float(data["acce1_z_v"])
        ax = float(data["acce1_x_g"])
        ay = float(data["acce1_y_g"])
        az = float(data["acce1_z_g"])
    except (KeyError, TypeError, ValueError):
        return None
    roll, pitch = accel_to_orientation(ax, ay, az)
    return {
        "pins": ACCE1_ADC_PINS,
        "mount_offset_mm": BODY_ACCEL_OFFSET_MM,
        "raw_gpio": {"x": None, "y": None, "z": None},
        "raw_voltage_v": {"x": vx, "y": vy, "z": vz},
        "ax_g": ax,
        "ay_g": ay,
        "az_g": az,
        "roll_deg": roll,
        "pitch_deg": pitch,
        "status": "ACCE1 from ESP32 ADC",
        "note": "AE-KXR94 via ESP32 ACCE1. Y/Z use ESP32 ADC2 pins and may be noisy while Wi-Fi/OTA is active.",
        "updated_s": now,
    }


def body_accel_gpio_reader(interval_s: float) -> None:
    """Read the body-mounted AE-KXR signal pins as digital GPIO.

    AE-KXR94-2050 outputs analog voltages. Raspberry Pi GPIO pins cannot measure
    those voltages directly, so this reader intentionally reports only digital
    high/low states and a clear status message. Add an external ADC to calculate
    voltage, acceleration, roll, and pitch.
    """
    if lgpio is None:
        merge_state(
            {
                "body_accel": {
                    "status": "lgpio not available; cannot read GPIO",
                    "updated_s": time.time(),
                }
            },
            "body_accel",
        )
        return

    handle = None
    try:
        handle = lgpio.gpiochip_open(0)
        for pin in BODY_ACCEL_PINS.values():
            lgpio.gpio_claim_input(handle, pin)
    except Exception as exc:
        merge_state(
            {
                "body_accel": {
                    "status": f"GPIO open failed: {exc}",
                    "updated_s": time.time(),
                }
            },
            "body_accel",
        )
        if handle is not None:
            try:
                lgpio.gpiochip_close(handle)
            except Exception:
                pass
        return

    while True:
        now = time.time()
        raw: dict[str, int | None] = {}
        try:
            for axis, pin in BODY_ACCEL_PINS.items():
                raw[axis] = int(lgpio.gpio_read(handle, pin))
            status = "digital GPIO only; ADC required for analog tilt"
        except Exception as exc:
            raw = {"x": None, "y": None, "z": None}
            status = f"GPIO read failed: {exc}"
        merge_state(
            {
                "body_accel": {
                    "pins": BODY_ACCEL_PINS,
                    "mount_offset_mm": BODY_ACCEL_OFFSET_MM,
                    "raw_gpio": raw,
                    "raw_voltage_v": {"x": None, "y": None, "z": None},
                    "ax_g": None,
                    "ay_g": None,
                    "az_g": None,
                    "roll_deg": None,
                    "pitch_deg": None,
                    "status": status,
                    "note": "Direct GPIO wiring shows only 0/1. Use MCP3008/ADS1115 to read AE-KXR94 analog voltages.",
                    "updated_s": now,
                }
            },
            "body_accel",
        )
        time.sleep(interval_s)


def parse_esp1(raw: str) -> dict[str, Any]:
    m = ESP1_RE.search(raw)
    if not m:
        return {}
    now = time.time()
    ax = float(m.group(1))
    ay = float(m.group(2))
    az = float(m.group(3))
    ki_signal = int(m.group(4))
    roll, pitch = accel_to_orientation(ax, ay, az)
    edge_hz, edge_rpm = estimate_grinder_from_signal(ki_signal, now)
    extras = parse_extra_key_values(raw)
    pulse_hz = extras.get("ki_hz", extras.get("ki1233_hz", edge_hz))
    grinder_rpm = extras.get("grinder_rpm", extras.get("ki1233_rpm", edge_rpm))
    rpm_source = "esp" if "grinder_rpm" in extras or "ki1233_rpm" in extras or "ki_hz" in extras or "ki1233_hz" in extras else "edge_estimate"
    if grinder_rpm is None:
        rpm_source = "waiting_edges"
    return {
        "imu": {
            "ax_g": ax,
            "ay_g": ay,
            "az_g": az,
            "roll_deg": roll,
            "pitch_deg": pitch,
            "yaw_deg": 0.0,
            "last_raw": raw,
            "updated_s": now,
        },
        "sensors": {
            "photo_gate": ki_signal,
            "ki1233_signal": ki_signal,
            "ki1233_pulse_hz": pulse_hz,
            "grinder_rpm": grinder_rpm,
            "grinder_rpm_source": rpm_source,
            "distance_mm": int(m.group(5)),
            "pressure_mpa": float(m.group(6)),
        },
    }


def parse_esp2(raw: str) -> dict[str, Any]:
    if not raw.startswith("TEL,"):
        return {}
    now = time.time()
    data: dict[str, Any] = {"last_raw": raw, "updated_s": now}
    for part in raw[4:].split(","):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        key = key.strip()
        parsed_value = coerce_value(value.strip())
        if key == "valve_mask" or key.startswith("valve_"):
            continue
        else:
            data[key] = parsed_value
    update: dict[str, Any] = {"motor": data}
    body_accel = build_body_accel_from_esp2(data, now)
    if body_accel is not None:
        update["body_accel"] = body_accel
    return update


def merge_state(update: dict[str, Any], source: str) -> None:
    now = time.time()
    with LOCK:
        STATE["server_time"] = datetime.now().isoformat(timespec="milliseconds")
        port_state = STATE["ports"].get(source, {})
        if not isinstance(port_state, dict):
            port_state = {}
        port_state["updated_s"] = now
        STATE["ports"][source] = port_state
        for section, values in update.items():
            if isinstance(values, dict) and isinstance(STATE.get(section), dict):
                STATE[section].update(values)
            else:
                STATE[section] = values


def send_serial_line(source: str, line: str) -> tuple[bool, str]:
    clean = line.strip()
    if not clean:
        return False, "empty line"
    with SERIAL_LOCK:
        ser = SERIAL_PORTS.get(source)
        if ser is None:
            return False, f"{source} serial is not open"
        try:
            ser.write((clean + "\n").encode("ascii"))
            ser.flush()
        except Exception as exc:
            return False, str(exc)
    return True, clean


def valve_level(on: bool) -> int:
    return 1 if (on == VALVE_ACTIVE_HIGH) else 0


def valve_state_payload(raw: str = "") -> dict[str, Any]:
    values = {state_key: int(VALVE_ON[name]) for name, state_key in VALVE_STATE_KEYS.items()}
    mask = 0
    for i, name in enumerate(VALVE_GPIO_PINS):
        if VALVE_ON[name]:
            mask |= 1 << i
    values.update({"mask": mask, "last_raw": raw, "updated_s": time.time()})
    return {"valves": values}


def pi_valve_init() -> tuple[bool, str]:
    global VALVE_CHIP
    if lgpio is None:
        return False, "lgpio not available"
    with VALVE_LOCK:
        if VALVE_CHIP is not None:
            return True, "already open"
        try:
            last_error = None
            for chip_number in VALVE_GPIO_CHIP_CANDIDATES:
                try:
                    VALVE_CHIP = lgpio.gpiochip_open(chip_number)
                    break
                except Exception as exc:
                    last_error = exc
            if VALVE_CHIP is None:
                raise RuntimeError(f"could not open gpiochip: {last_error}")
            for pin in VALVE_GPIO_PINS.values():
                lgpio.gpio_claim_output(VALVE_CHIP, pin, valve_level(False))
        except Exception as exc:
            VALVE_CHIP = None
            return False, str(exc)
    merge_state(
        {
            "ports": {
                "pi_valves": {
                    "status": "open",
                    "pins": VALVE_GPIO_PINS,
                    "active_high": VALVE_ACTIVE_HIGH,
                    "hold_timeout_s": VALVE_HOLD_TIMEOUT_S,
                }
            },
            **valve_state_payload("VALVE,set,name=ALL,on=0"),
        },
        "pi_valves",
    )
    return True, "open"


def pi_valve_write_unlocked(name: str, on: bool) -> None:
    if VALVE_CHIP is None:
        raise RuntimeError("Pi valve GPIO is not initialized")
    pin = VALVE_GPIO_PINS[name]
    lgpio.gpio_write(VALVE_CHIP, pin, valve_level(on))
    VALVE_ON[name] = on
    if on:
        VALVE_LAST_ON[name] = time.time()


def pi_valve_command(line: str) -> tuple[bool, str]:
    clean = line.strip()
    if not clean:
        return False, "empty line"
    parts = [part.strip() for part in clean.split(",")]
    if len(parts) < 2 or parts[0].upper() != "VALVE":
        return False, "not a valve command"

    ok, message = pi_valve_init()
    if not ok:
        return False, message

    name = parts[1].upper()
    with VALVE_LOCK:
        try:
            if name == "STATUS":
                merge_state(valve_state_payload("VALVE,STATUS"), "pi_valves")
                return True, "VALVE,STATUS"
            if name == "ALL":
                value = parts[2].upper() if len(parts) > 2 else "0"
                if value in {"1", "ON"}:
                    return False, "ALL on is not allowed"
                for valve_name in VALVE_GPIO_PINS:
                    pi_valve_write_unlocked(valve_name, False)
                msg = "VALVE,set,name=ALL,on=0"
                merge_state(valve_state_payload(msg), "pi_valves")
                return True, msg
            if name not in VALVE_GPIO_PINS:
                return False, f"unknown valve: {name}"
            if len(parts) < 3:
                return False, "missing value"
            value = parts[2].upper()
            if value not in {"0", "1", "ON", "OFF"}:
                return False, f"bad value: {parts[2]}"
            on = value in {"1", "ON"}
            if on and name in VALVE_INTERLOCKS:
                pi_valve_write_unlocked(VALVE_INTERLOCKS[name], False)
            pi_valve_write_unlocked(name, on)
            msg = f"VALVE,set,name={name},on={1 if on else 0},pin={VALVE_GPIO_PINS[name]}"
            merge_state(valve_state_payload(msg), "pi_valves")
            return True, msg
        except Exception as exc:
            return False, str(exc)


def pi_valve_watchdog() -> None:
    ok, message = pi_valve_init()
    if not ok:
        merge_state({"ports": {"pi_valves": {"status": f"open failed: {message}"}}}, "pi_valves")
        return
    while True:
        changed = False
        now = time.time()
        with VALVE_LOCK:
            for name, on in list(VALVE_ON.items()):
                if on and now - VALVE_LAST_ON[name] > VALVE_HOLD_TIMEOUT_S:
                    try:
                        pi_valve_write_unlocked(name, False)
                        changed = True
                    except Exception as exc:
                        merge_state(
                            {"ports": {"pi_valves": {"status": f"write failed: {exc}"}}},
                            "pi_valves",
                        )
            if changed:
                merge_state(valve_state_payload("VALVE,timeout"), "pi_valves")
        time.sleep(0.05)


def serial_reader(name: str, port: str, baud: int) -> None:
    if serial is None:
        merge_state({"ports": {name: {"error": "pyserial not available"}}}, name)
        return

    while True:
        try:
            ser = serial.Serial(port, baud, timeout=0.5)
            with SERIAL_LOCK:
                SERIAL_PORTS[name] = ser
            merge_state({"ports": {name: {"port": port, "status": "open"}}}, name)
        except Exception as exc:
            merge_state({"ports": {name: {"port": port, "status": f"open failed: {exc}"}}}, name)
            time.sleep(2.0)
            continue

        try:
            while True:
                raw = ser.readline().decode(errors="replace").strip()
                if not raw:
                    continue
                if name == "esp1":
                    update = parse_esp1(raw)
                else:
                    update = parse_esp2(raw)
                if update:
                    merge_state(update, name)
        except Exception as exc:
            merge_state({"ports": {name: {"port": port, "status": f"read error: {exc}"}}}, name)
            try:
                with SERIAL_LOCK:
                    if SERIAL_PORTS.get(name) is ser:
                        SERIAL_PORTS.pop(name, None)
                ser.close()
            except Exception:
                pass
            time.sleep(1.0)


def udp_command_reader(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    merge_state({"ports": {"command_udp": {"port": port, "status": "open"}}}, "command_udp")

    while True:
        try:
            packet, _addr = sock.recvfrom(8192)
            raw = packet.decode("utf-8", errors="replace").strip()
            lines: list[str]
            if raw.startswith("{"):
                payload = json.loads(raw)
                value = payload.get("lines", payload.get("line", []))
                if isinstance(value, str):
                    lines = [value]
                elif isinstance(value, list):
                    lines = [str(item) for item in value]
                else:
                    lines = []
            else:
                lines = [raw]
            for line in lines:
                if line.strip().upper().startswith("VALVE,"):
                    ok, message = pi_valve_command(line)
                else:
                    ok, message = send_serial_line("esp2", line)
                if not ok:
                    merge_state(
                        {"ports": {"command_udp": {"port": port, "status": f"write failed: {message}"}}},
                        "command_udp",
                    )
        except Exception as exc:
            merge_state(
                {"ports": {"command_udp": {"port": port, "status": f"read error: {exc}"}}},
                "command_udp",
            )
            time.sleep(0.1)


def udp_controller_reader(port: int) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    merge_state({"ports": {"controller_udp": {"port": port, "status": "open"}}}, "controller_udp")

    while True:
        try:
            packet, addr = sock.recvfrom(8192)
            raw = packet.decode("utf-8", errors="replace")
            payload = json.loads(raw)
            buttons = payload.get("buttons") or []
            pressed = [
                i for i, value in enumerate(buttons)
                if isinstance(value, (int, float, bool)) and bool(value)
            ]
            update = {
                "controller": {
                    "name": str(payload.get("name", "")),
                    "source_ip": addr[0],
                    "seq": payload.get("seq"),
                    "axes": payload.get("axes") or [],
                    "buttons": buttons,
                    "pressed_buttons": pressed,
                    "hats": payload.get("hats") or [],
                    "last_raw": raw,
                    "updated_s": time.time(),
                }
            }
            merge_state(update, "controller_udp")
        except Exception as exc:
            merge_state(
                {"ports": {"controller_udp": {"port": port, "status": f"read error: {exc}"}}},
                "controller_udp",
            )
            time.sleep(0.1)


class DashboardHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            with LOCK:
                payload = json.dumps(STATE, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/api/valve":
            query = parse_qs(parsed.query)
            name = (query.get("name") or [""])[0].strip().upper()
            on = (query.get("on") or query.get("value") or ["0"])[0].strip()
            allowed = {
                "MOVE_PUSH",
                "MOVE_PULL",
                "DRILL_PUSH",
                "DRILL_PULL",
                "GRINDER_AIR",
                "ALL",
                "STATUS",
            }
            if name not in allowed or on not in {"0", "1", "ON", "OFF", "on", "off"}:
                payload = json.dumps({"ok": False, "error": "bad valve request"}).encode("utf-8")
                self.send_response(400)
            else:
                line = "VALVE,STATUS" if name == "STATUS" else f"VALVE,{name},{on}"
                ok, message = pi_valve_command(line)
                payload = json.dumps({"ok": ok, "message": message}).encode("utf-8")
                self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if parsed.path == "/":
            self.path = "/robot_dashboard.html"
        super().do_GET()

    def log_message(self, fmt: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Pipe robot camera/status dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--esp1-port", default="/dev/ttyAMA2")
    parser.add_argument("--esp2-port", default="/dev/ttyAMA4")
    parser.add_argument("--controller-udp-port", type=int, default=8091)
    parser.add_argument("--command-udp-port", type=int, default=8092)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--body-accel-interval", type=float, default=0.1)
    parser.add_argument("--body-accel-gpio", action="store_true")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent))
    args = parser.parse_args()

    os.chdir(args.root)

    threading.Thread(
        target=serial_reader, args=("esp1", args.esp1_port, args.baud), daemon=True
    ).start()
    threading.Thread(
        target=serial_reader, args=("esp2", args.esp2_port, args.baud), daemon=True
    ).start()
    threading.Thread(
        target=udp_controller_reader, args=(args.controller_udp_port,), daemon=True
    ).start()
    threading.Thread(
        target=udp_command_reader, args=(args.command_udp_port,), daemon=True
    ).start()
    threading.Thread(target=pi_valve_watchdog, daemon=True).start()
    if args.body_accel_gpio:
        threading.Thread(
            target=body_accel_gpio_reader, args=(args.body_accel_interval,), daemon=True
        ).start()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard listening on http://{args.host}:{args.port}/robot_dashboard.html")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
