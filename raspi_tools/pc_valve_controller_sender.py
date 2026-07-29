from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import threading
import time
import urllib.request


DEFAULT_BUTTON_MAP = {
    "drill_push": 7,
    "drill_pull": 6,
    "move_push": 9,
    "move_pull": 8,
}
DEFAULT_GRINDER_ON_BUTTON = 10
DEFAULT_GRINDER_OFF_BUTTON = 11
DEFAULT_PRESSURE_TARGET_MPA = 0.60
DEFAULT_PRESSURE_HYSTERESIS_MPA = 0.02

VALVE_COMMAND_NAMES = {
    "move_push": "MOVE_PUSH",
    "move_pull": "MOVE_PULL",
    "drill_push": "DRILL_PUSH",
    "drill_pull": "DRILL_PULL",
    "grinder_air": "GRINDER_AIR",
}

AXIS_CENTER = 512
AXIS_RANGE_HALF = 511
MOTOR_COMMAND_DEADZONE = 50


def load_pygame():
    try:
        import pygame  # type: ignore
    except Exception as exc:
        print("[ERROR] pygame is required to read the controller.")
        print("Install with:")
        print(f"  {sys.executable} -m pip install pygame")
        print(f"Import error: {exc}")
        raise SystemExit(1)
    return pygame


def parse_map(items: list[str]) -> dict[str, int]:
    mapping = dict(DEFAULT_BUTTON_MAP)
    for item in items:
        if "=" not in item:
            raise ValueError(f"Bad --map value: {item}")
        valve, button = item.split("=", 1)
        valve = valve.strip().lower()
        if valve not in VALVE_COMMAND_NAMES:
            raise ValueError(f"Unknown valve name: {valve}")
        mapping[valve] = int(button.strip())
    return mapping


def send_command(sock: socket.socket, target: tuple[str, int], lines: list[str]) -> None:
    payload = json.dumps({"lines": lines}, separators=(",", ":")).encode("utf-8")
    sock.sendto(payload, target)


def read_pressure_mpa(host: str, dashboard_port: int, timeout_s: float, stale_s: float) -> float:
    url = f"http://{host}:{dashboard_port}/api/state"
    with urllib.request.urlopen(url, timeout=timeout_s) as response:
        payload = json.load(response)

    sensors = payload.get("sensors") if isinstance(payload.get("sensors"), dict) else {}
    imu = payload.get("imu") if isinstance(payload.get("imu"), dict) else {}
    pressure = float(sensors.get("pressure_mpa"))
    updated_s = float(imu.get("updated_s"))
    age_s = max(0.0, time.time() - updated_s)
    if not math.isfinite(pressure) or pressure < 0.0 or pressure > 1.5:
        raise ValueError(f"invalid pressure value: {pressure!r}")
    if not math.isfinite(updated_s) or age_s > stale_s:
        raise ValueError(f"stale pressure value: age={age_s:.3f}s")
    return pressure


def pressure_regulated_output(
    *,
    requested: bool,
    retracting: bool,
    pressure_mpa: float | None,
    previous_on: bool,
    target_mpa: float,
    hysteresis_mpa: float,
) -> bool:
    if not requested or retracting or pressure_mpa is None:
        return False
    if pressure_mpa >= target_mpa:
        return False
    if pressure_mpa <= target_mpa - hysteresis_mpa:
        return True
    return previous_on


def pressure_regulation_reason(
    *,
    requested: bool,
    retracting: bool,
    pressure_mpa: float | None,
    previous_on: bool,
    target_mpa: float,
    hysteresis_mpa: float,
) -> str:
    if not requested:
        return "button_released"
    if retracting:
        return "retract_override"
    if pressure_mpa is None:
        return "pressure_invalid"
    if pressure_mpa >= target_mpa:
        return "target_reached"
    if pressure_mpa <= target_mpa - hysteresis_mpa:
        return "below_lower_limit"
    return "hysteresis_hold_on" if previous_on else "hysteresis_hold_off"


def send_controller_state(
    sock: socket.socket,
    target: tuple[str, int],
    name: str,
    seq: int,
    axes: list[float],
    buttons: list[int],
    hats: list[list[int]],
) -> None:
    payload = {
        "seq": seq,
        "pc_time": time.time(),
        "name": name,
        "axes": axes,
        "buttons": buttons,
        "pressed_buttons": [i for i, value in enumerate(buttons) if value],
        "hats": hats,
    }
    sock.sendto(json.dumps(payload, separators=(",", ":")).encode("utf-8"), target)


def line_for(valve: str, on: bool) -> str:
    return f"VALVE,{VALVE_COMMAND_NAMES[valve]},{1 if on else 0}"


def apply_axis_deadzone(value: float, deadzone: float) -> float:
    if abs(value) < deadzone:
        return 0.0
    sign = 1.0 if value >= 0.0 else -1.0
    scaled = (abs(value) - deadzone) / max(1e-6, 1.0 - deadzone)
    return sign * min(1.0, scaled)


def axis_to_abs(
    value: float,
    *,
    deadzone: float,
    invert: bool,
    response_exponent: float = 1.0,
) -> int:
    value = apply_axis_deadzone(value, deadzone)
    value = math.copysign(abs(value) ** response_exponent, value)
    if invert:
        value = -value
    return max(0, min(1023, int(round(AXIS_CENTER + value * AXIS_RANGE_HALF))))


def axis_to_unipolar_abs(
    value: float,
    *,
    invert: bool,
    response_exponent: float = 1.0,
) -> int:
    value = max(-1.0, min(1.0, value))
    if invert:
        value = -value
    throttle = (value + 1.0) * 0.5
    throttle = throttle**response_exponent
    return max(
        AXIS_CENTER,
        min(1023, int(round(AXIS_CENTER + throttle * AXIS_RANGE_HALF))),
    )


def throttle_direction_to_abs(
    direction_value: float,
    throttle_value: float,
    *,
    direction_deadzone: float,
    invert_direction: bool,
    throttle_full_scale: float,
    motor_max_duty_ratio: float,
) -> int:
    if abs(direction_value) < direction_deadzone:
        return AXIS_CENTER

    throttle_value = max(-1.0, min(throttle_full_scale, throttle_value))
    throttle_ratio = (throttle_value + 1.0) / (throttle_full_scale + 1.0)
    throttle_ratio = max(0.0, min(1.0, throttle_ratio))
    duty_ratio = throttle_ratio * motor_max_duty_ratio
    if duty_ratio <= 0.0:
        return AXIS_CENTER

    if invert_direction:
        direction_value = -direction_value
    direction_sign = 1 if direction_value > 0.0 else -1

    # ESP32 removes a 50-count command deadzone before converting the
    # remaining 461 counts to duty 0..255. Add that deadzone back here so
    # throttle travel and the resulting duty ratio remain linear.
    command_offset = int(
        round(
            MOTOR_COMMAND_DEADZONE
            + duty_ratio * (AXIS_RANGE_HALF - MOTOR_COMMAND_DEADZONE)
        )
    )
    return max(
        0,
        min(1023, AXIS_CENTER + direction_sign * command_offset),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Read a PC game controller and operate solenoid valves through "
            "the Raspberry Pi dashboard command UDP port. Valves are held ON "
            "only while mapped buttons are pressed."
        )
    )
    parser.add_argument("--pi-host", default="192.168.50.154")
    parser.add_argument("--command-port", type=int, default=8092)
    parser.add_argument("--controller-port", type=int, default=8091)
    parser.add_argument("--dashboard-port", type=int, default=8090)
    parser.add_argument("--controller-index", type=int, default=0)
    parser.add_argument("--rate-hz", type=float, default=20.0)
    parser.add_argument("--hold-period-s", type=float, default=0.05)
    parser.add_argument("--deadzone", type=float, default=0.18)
    parser.add_argument("--step-axis", type=int, default=0, help="Left/right axis index for the stepper.")
    parser.add_argument("--motor-axis", type=int, default=1, help="Forward/back axis index for the DC motor.")
    parser.add_argument(
        "--motor-response-exponent",
        type=float,
        default=1.35,
        help=(
            "Shape the DC motor joystick response. Values above 1 move high speed "
            "closer to full stick; 1 is linear."
        ),
    )
    parser.add_argument(
        "--motor-unipolar",
        action="store_true",
        help="Map motor axis -1 to stop and +1 to maximum forward speed.",
    )
    parser.add_argument(
        "--motor-throttle-axis",
        type=int,
        help="Separate axis used only for DC motor duty; motor-axis selects direction.",
    )
    parser.add_argument(
        "--motor-throttle-full-scale",
        type=float,
        default=0.95,
        help="Throttle input that produces 100%% duty in separate-throttle mode.",
    )
    parser.add_argument(
        "--invert-motor-throttle",
        action="store_true",
        help="Reverse the sign of the separate DC motor throttle axis.",
    )
    parser.add_argument(
        "--motor-max-duty-ratio",
        type=float,
        default=1.0,
        help="Maximum PWM duty ratio used at full throttle (0 < value <= 1).",
    )
    parser.add_argument(
        "--motor-full-speed-hat",
        type=int,
        default=0,
        help=(
            "Hat index whose vertical +1/-1 input commands maximum forward/reverse "
            "speed. Use -1 to disable."
        ),
    )
    parser.add_argument("--invert-step", action="store_true", help="Reverse stepper axis direction.")
    parser.add_argument(
        "--no-invert-motor",
        action="store_true",
        help="Do not invert the motor axis. Default assumes joystick forward is negative.",
    )
    parser.add_argument("--axis-period-s", type=float, default=0.05)
    parser.add_argument("--pressure-target-mpa", type=float, default=DEFAULT_PRESSURE_TARGET_MPA)
    parser.add_argument(
        "--pressure-hysteresis-mpa",
        type=float,
        default=DEFAULT_PRESSURE_HYSTERESIS_MPA,
    )
    parser.add_argument("--pressure-poll-period-s", type=float, default=0.10)
    parser.add_argument("--pressure-timeout-s", type=float, default=0.08)
    parser.add_argument("--pressure-stale-s", type=float, default=0.50)
    pressure_mode = parser.add_mutually_exclusive_group()
    pressure_mode.add_argument(
        "--pressure-control",
        action="store_true",
        help=(
            "Enable 0.6 MPa DRILL_PUSH regulation for roll-position-change operation. "
            "Normal operation uses direct momentary control."
        ),
    )
    pressure_mode.add_argument(
        "--no-pressure-control",
        action="store_false",
        dest="pressure_control",
        help=argparse.SUPPRESS,
    )
    parser.set_defaults(pressure_control=False)
    parser.add_argument("--list", action="store_true", help="List controllers and exit.")
    parser.add_argument(
        "--toggle",
        action="append",
        default=[],
        metavar="VALVE",
        help=(
            "Make a valve toggle on each button press instead of momentary. "
            "Default: grinder_air. Use --no-default-toggle to disable the default."
        ),
    )
    parser.add_argument("--no-default-toggle", action="store_true")
    parser.add_argument("--grinder-on-button", type=int, default=DEFAULT_GRINDER_ON_BUTTON)
    parser.add_argument("--grinder-off-button", type=int, default=DEFAULT_GRINDER_OFF_BUTTON)
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="VALVE=BUTTON_INDEX",
        help=(
            "Override button mapping. Example: --map move_push=5. "
            "Valve names: move_push, move_pull, drill_push, drill_pull, grinder_air"
        ),
    )
    args = parser.parse_args()
    if args.pressure_target_mpa <= 0.0:
        parser.error("--pressure-target-mpa must be positive")
    if not 0.0 < args.pressure_hysteresis_mpa < args.pressure_target_mpa:
        parser.error("--pressure-hysteresis-mpa must be between 0 and the target")
    if args.pressure_poll_period_s <= 0.0:
        parser.error("--pressure-poll-period-s must be positive")
    if args.motor_response_exponent < 1.0:
        parser.error("--motor-response-exponent must be at least 1.0")
    if not -1.0 < args.motor_throttle_full_scale <= 1.0:
        parser.error("--motor-throttle-full-scale must be greater than -1 and at most 1")
    if not 0.0 < args.motor_max_duty_ratio <= 1.0:
        parser.error("--motor-max-duty-ratio must be greater than 0 and at most 1")

    pygame = load_pygame()
    pygame.init()
    pygame.joystick.init()

    count = pygame.joystick.get_count()
    if args.list:
        print(f"[controllers] count={count}")
        for i in range(count):
            js = pygame.joystick.Joystick(i)
            js.init()
            print(
                f"  {i}: {js.get_name()} "
                f"axes={js.get_numaxes()} buttons={js.get_numbuttons()} hats={js.get_numhats()}"
            )
        pygame.quit()
        return 0

    if count <= 0:
        print("[ERROR] No controller found.")
        print("Check Windows game controller settings or reconnect the controller.")
        pygame.quit()
        return 1

    if args.controller_index >= count:
        print(f"[ERROR] controller index {args.controller_index} out of range. found={count}")
        pygame.quit()
        return 1

    button_map = parse_map(args.map)
    toggle_valves = set(args.toggle)
    if not args.no_default_toggle:
        toggle_valves.add("grinder_air")
    unknown_toggles = sorted(toggle_valves - set(VALVE_COMMAND_NAMES))
    if unknown_toggles:
        print(f"[ERROR] Unknown toggle valve(s): {', '.join(unknown_toggles)}")
        pygame.quit()
        return 1
    joystick = pygame.joystick.Joystick(args.controller_index)
    joystick.init()

    command_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    state_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    command_target = (args.pi_host, args.command_port)
    state_target = (args.pi_host, args.controller_port)

    interval = 1.0 / max(args.rate_hz, 1.0)
    seq = 0
    last_hold = 0.0
    last_axis = 0.0
    prev_abs_x: int | None = None
    prev_abs_y: int | None = None
    prev_valve_state = {valve: False for valve in VALVE_COMMAND_NAMES}
    motor_uses_throttle = args.motor_throttle_axis is not None or args.motor_unipolar
    motor_armed = not motor_uses_throttle
    toggle_state = {valve: False for valve in toggle_valves}
    prev_buttons: list[int] = []
    pressure_lock = threading.Lock()
    pressure_state: dict[str, float | str | None] = {
        "value": None,
        "error": "not read",
        "success_monotonic": 0.0,
    }
    pressure_stop = threading.Event()
    pressure_thread: threading.Thread | None = None
    last_control_signature: tuple[object, ...] | None = None

    def pressure_poll_loop() -> None:
        while not pressure_stop.is_set():
            started = time.monotonic()
            try:
                value = read_pressure_mpa(
                    args.pi_host,
                    args.dashboard_port,
                    args.pressure_timeout_s,
                    args.pressure_stale_s,
                )
                with pressure_lock:
                    pressure_state["value"] = value
                    pressure_state["error"] = ""
                    pressure_state["success_monotonic"] = time.monotonic()
            except Exception as exc:
                with pressure_lock:
                    pressure_state["error"] = str(exc)
            elapsed = time.monotonic() - started
            pressure_stop.wait(max(0.0, args.pressure_poll_period_s - elapsed))

    if args.pressure_control:
        pressure_thread = threading.Thread(
            target=pressure_poll_loop,
            name="pressure-poll",
            daemon=True,
        )
        pressure_thread.start()

    print(f"[controller] {joystick.get_name()}")
    print(f"[udp] command -> {args.pi_host}:{args.command_port}")
    print(f"[udp] display -> {args.pi_host}:{args.controller_port}")
    print("[valve map]")
    for valve, index in button_map.items():
        mode = "toggle" if valve in toggle_valves else "hold"
        print(f"  button {index:>2} -> {VALVE_COMMAND_NAMES[valve]} ({mode})")
    print(f"  button {args.grinder_on_button:>2} -> GRINDER_AIR ON")
    print(f"  button {args.grinder_off_button:>2} -> GRINDER_AIR OFF")
    if not args.pressure_control:
        print("  DRILL_PUSH pressure control: disabled (normal operation)")
    else:
        print(
            "  DRILL_PUSH pressure control: enabled (roll-position-change operation), "
            f"ON <= {args.pressure_target_mpa - args.pressure_hysteresis_mpa:.3f} MPa, "
            f"OFF >= {args.pressure_target_mpa:.3f} MPa"
        )
    print("[axis map]")
    print(f"  axis {args.step_axis} left/right -> AXIS,ABS_X stepper speed")
    if args.motor_throttle_axis is not None:
        print(
            f"  axis {args.motor_axis} forward/back -> DC motor direction; "
            f"axis {args.motor_throttle_axis} -> linear speed 0..configured maximum"
        )
        print(
            f"  throttle -1=0%, +{args.motor_throttle_full_scale:g}="
            f"{args.motor_max_duty_ratio * 100:.1f}% PWM (configured maximum)"
        )
        if args.invert_motor_throttle:
            print("  throttle sign: inverted (physical + side is stop)")
        if args.motor_full_speed_hat >= 0:
            print(
                f"  hat {args.motor_full_speed_hat} up/down -> "
                "maximum forward/reverse speed"
            )
        print("  motor start interlock: move the throttle axis to -1 once")
    elif args.motor_unipolar:
        print(
            f"  axis {args.motor_axis} -1=stop, +1=maximum "
            "-> AXIS,ABS_Y DC motor speed"
        )
        print("  motor start interlock: move the axis to -1 once to enable output")
    else:
        print(f"  axis {args.motor_axis} forward/back -> AXIS,ABS_Y DC motor speed")
    print(f"  deadzone={args.deadzone:g}")
    print(f"  motor response exponent={args.motor_response_exponent:g}")
    print("[RUN] Hold mapped buttons for cylinders. Ctrl+C stops all valves and motors.")

    def all_off() -> None:
        send_command(command_sock, command_target, ["AXIS,ABS_X,512", "AXIS,ABS_Y,512", "VALVE,ALL,0"])
        for valve in prev_valve_state:
            prev_valve_state[valve] = False
        for valve in toggle_state:
            toggle_state[valve] = False

    try:
        all_off()
        while True:
            pygame.event.pump()

            axes = []
            raw_axes = []
            for i in range(joystick.get_numaxes()):
                value = float(joystick.get_axis(i))
                raw_axes.append(value)
                axes.append(round(apply_axis_deadzone(value, args.deadzone), 4))

            buttons = [int(joystick.get_button(i)) for i in range(joystick.get_numbuttons())]
            hats = [list(joystick.get_hat(i)) for i in range(joystick.get_numhats())]
            if not prev_buttons:
                prev_buttons = [0 for _ in buttons]

            now = time.monotonic()
            with pressure_lock:
                pressure_mpa_value = pressure_state["value"]
                pressure_error = str(pressure_state["error"] or "")
                pressure_success = float(pressure_state["success_monotonic"] or 0.0)
            pressure_mpa = (
                float(pressure_mpa_value)
                if pressure_mpa_value is not None
                and now - pressure_success <= args.pressure_stale_s
                else None
            )

            desired: dict[str, bool] = {}
            for valve, button_index in button_map.items():
                pressed = bool(button_index < len(buttons) and buttons[button_index])
                was_pressed = bool(button_index < len(prev_buttons) and prev_buttons[button_index])
                if valve in toggle_valves:
                    if pressed and not was_pressed:
                        toggle_state[valve] = not toggle_state.get(valve, False)
                    desired[valve] = toggle_state.get(valve, False)
                else:
                    desired[valve] = pressed

            grinder_on_pressed = bool(args.grinder_on_button < len(buttons) and buttons[args.grinder_on_button])
            grinder_on_was_pressed = bool(
                args.grinder_on_button < len(prev_buttons) and prev_buttons[args.grinder_on_button]
            )
            grinder_off_pressed = bool(args.grinder_off_button < len(buttons) and buttons[args.grinder_off_button])
            grinder_off_was_pressed = bool(
                args.grinder_off_button < len(prev_buttons) and prev_buttons[args.grinder_off_button]
            )
            if grinder_on_pressed and not grinder_on_was_pressed:
                toggle_state["grinder_air"] = True
            if grinder_off_pressed and not grinder_off_was_pressed:
                toggle_state["grinder_air"] = False
            desired["grinder_air"] = toggle_state.get("grinder_air", False)

            drill_push_requested = desired.get("drill_push", False)
            pressure_reason = "pressure_control_disabled"
            if args.pressure_control:
                pressure_reason = pressure_regulation_reason(
                    requested=drill_push_requested,
                    retracting=desired.get("drill_pull", False),
                    pressure_mpa=pressure_mpa,
                    previous_on=prev_valve_state.get("drill_push", False),
                    target_mpa=args.pressure_target_mpa,
                    hysteresis_mpa=args.pressure_hysteresis_mpa,
                )
                desired["drill_push"] = pressure_regulated_output(
                    requested=drill_push_requested,
                    retracting=desired.get("drill_pull", False),
                    pressure_mpa=pressure_mpa,
                    previous_on=prev_valve_state.get("drill_push", False),
                    target_mpa=args.pressure_target_mpa,
                    hysteresis_mpa=args.pressure_hysteresis_mpa,
                )

            lines: list[str] = []
            step_value = raw_axes[args.step_axis] if args.step_axis < len(raw_axes) else 0.0
            motor_value = raw_axes[args.motor_axis] if args.motor_axis < len(raw_axes) else 0.0
            abs_x = axis_to_abs(step_value, deadzone=args.deadzone, invert=args.invert_step)
            if args.motor_throttle_axis is not None:
                throttle_value = (
                    raw_axes[args.motor_throttle_axis]
                    if args.motor_throttle_axis < len(raw_axes)
                    else -1.0
                )
                if args.invert_motor_throttle:
                    throttle_value = -throttle_value
                if not motor_armed and throttle_value <= -0.90:
                    motor_armed = True
                    print("[MOTOR] throttle reached low position; DC motor output enabled")
                if motor_armed:
                    abs_y = throttle_direction_to_abs(
                        motor_value,
                        throttle_value,
                        direction_deadzone=args.deadzone,
                        invert_direction=not args.no_invert_motor,
                        throttle_full_scale=args.motor_throttle_full_scale,
                        motor_max_duty_ratio=args.motor_max_duty_ratio,
                    )
                else:
                    abs_y = AXIS_CENTER
            elif args.motor_unipolar:
                if not motor_armed and motor_value <= -0.90:
                    motor_armed = True
                    print("[MOTOR] throttle reached low position; DC motor output enabled")
                if motor_armed:
                    abs_y = axis_to_unipolar_abs(
                        motor_value,
                        invert=False,
                        response_exponent=args.motor_response_exponent,
                    )
                else:
                    abs_y = AXIS_CENTER
            else:
                abs_y = axis_to_abs(
                    motor_value,
                    deadzone=args.deadzone,
                    invert=not args.no_invert_motor,
                    response_exponent=args.motor_response_exponent,
                )

            if (
                motor_armed
                and args.motor_full_speed_hat >= 0
                and args.motor_full_speed_hat < len(hats)
                and len(hats[args.motor_full_speed_hat]) >= 2
            ):
                hat_y = int(hats[args.motor_full_speed_hat][1])
                if hat_y != 0:
                    # Pygame hats report up=(0,+1), down=(0,-1). Convert this
                    # to the same forward/reverse convention as motor-axis.
                    abs_y = throttle_direction_to_abs(
                        -float(hat_y),
                        1.0,
                        direction_deadzone=args.deadzone,
                        invert_direction=not args.no_invert_motor,
                        throttle_full_scale=1.0,
                        motor_max_duty_ratio=1.0,
                    )
            if abs_x != prev_abs_x or abs_y != prev_abs_y or now - last_axis >= args.axis_period_s:
                lines.extend([f"AXIS,ABS_X,{abs_x}", f"AXIS,ABS_Y,{abs_y}"])
                prev_abs_x = abs_x
                prev_abs_y = abs_y
                last_axis = now

            for valve, on in desired.items():
                if on != prev_valve_state.get(valve, False):
                    lines.append(line_for(valve, on))
                    prev_valve_state[valve] = on

            if now - last_hold >= args.hold_period_s:
                for valve, on in desired.items():
                    if on:
                        lines.append(line_for(valve, True))
                last_hold = now

            if lines:
                send_command(command_sock, command_target, lines)

            send_controller_state(
                state_sock,
                state_target,
                joystick.get_name(),
                seq,
                axes,
                buttons,
                hats,
            )

            active = [VALVE_COMMAND_NAMES[valve] for valve, on in desired.items() if on]
            pressed = [i for i, value in enumerate(buttons) if value]
            pressure_text = (
                f"{pressure_mpa:.3f}MPa"
                if pressure_mpa is not None
                else f"invalid({pressure_error})"
            )
            pressure_source = "fresh" if not pressure_error else f"cached_after:{pressure_error}"
            control_signature = (
                tuple(pressed),
                tuple((name, bool(on)) for name, on in desired.items()),
                pressure_reason,
                pressure_source,
            )
            if control_signature != last_control_signature:
                print(
                    "\n[CONTROL] "
                    f"pressed={pressed or ['-']} pressure={pressure_text} "
                    f"pressure_source={pressure_source} "
                    f"drill_push_request={int(drill_push_requested)} "
                    f"drill_push_output={int(desired.get('drill_push', False))} "
                    f"reason={pressure_reason} valves={active or ['-']}",
                    flush=True,
                )
                last_control_signature = control_signature
            print(
                f"\rseq={seq} abs_x={abs_x} abs_y={abs_y} "
                f"pressure={pressure_text} pressed={pressed} "
                f"valves={active or ['-']}      ",
                end="",
                flush=True,
            )
            seq += 1
            prev_buttons = buttons
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C")
    finally:
        try:
            all_off()
            time.sleep(0.1)
            all_off()
        finally:
            pressure_stop.set()
            if pressure_thread is not None:
                pressure_thread.join(timeout=1.0)
            command_sock.close()
            state_sock.close()
            pygame.quit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
