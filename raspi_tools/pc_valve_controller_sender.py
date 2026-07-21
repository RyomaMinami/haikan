from __future__ import annotations

import argparse
import json
import socket
import sys
import time


DEFAULT_BUTTON_MAP = {
    "drill_push": 0,
    "drill_pull": 1,
    "move_push": 3,
    "move_pull": 4,
}
DEFAULT_GRINDER_ON_BUTTON = 10
DEFAULT_GRINDER_OFF_BUTTON = 11

VALVE_COMMAND_NAMES = {
    "move_push": "MOVE_PUSH",
    "move_pull": "MOVE_PULL",
    "drill_push": "DRILL_PUSH",
    "drill_pull": "DRILL_PULL",
    "grinder_air": "GRINDER_AIR",
}

AXIS_CENTER = 512
AXIS_RANGE_HALF = 511


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


def axis_to_abs(value: float, *, deadzone: float, invert: bool) -> int:
    value = apply_axis_deadzone(value, deadzone)
    if invert:
        value = -value
    return max(0, min(1023, int(round(AXIS_CENTER + value * AXIS_RANGE_HALF))))


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
    parser.add_argument("--controller-index", type=int, default=0)
    parser.add_argument("--rate-hz", type=float, default=20.0)
    parser.add_argument("--hold-period-s", type=float, default=0.1)
    parser.add_argument("--deadzone", type=float, default=0.18)
    parser.add_argument("--step-axis", type=int, default=0, help="Left/right axis index for the stepper.")
    parser.add_argument("--motor-axis", type=int, default=1, help="Forward/back axis index for the DC motor.")
    parser.add_argument("--invert-step", action="store_true", help="Reverse stepper axis direction.")
    parser.add_argument(
        "--no-invert-motor",
        action="store_true",
        help="Do not invert the motor axis. Default assumes joystick forward is negative.",
    )
    parser.add_argument("--axis-period-s", type=float, default=0.05)
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
    toggle_state = {valve: False for valve in toggle_valves}
    prev_buttons: list[int] = []

    print(f"[controller] {joystick.get_name()}")
    print(f"[udp] command -> {args.pi_host}:{args.command_port}")
    print(f"[udp] display -> {args.pi_host}:{args.controller_port}")
    print("[valve map]")
    for valve, index in button_map.items():
        mode = "toggle" if valve in toggle_valves else "hold"
        print(f"  button {index:>2} -> {VALVE_COMMAND_NAMES[valve]} ({mode})")
    print(f"  button {args.grinder_on_button:>2} -> GRINDER_AIR ON")
    print(f"  button {args.grinder_off_button:>2} -> GRINDER_AIR OFF")
    print("[axis map]")
    print(f"  axis {args.step_axis} left/right -> AXIS,ABS_X stepper speed")
    print(f"  axis {args.motor_axis} forward/back -> AXIS,ABS_Y DC motor speed")
    print(f"  deadzone={args.deadzone:g}")
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

            lines: list[str] = []
            step_value = raw_axes[args.step_axis] if args.step_axis < len(raw_axes) else 0.0
            motor_value = raw_axes[args.motor_axis] if args.motor_axis < len(raw_axes) else 0.0
            abs_x = axis_to_abs(step_value, deadzone=args.deadzone, invert=args.invert_step)
            abs_y = axis_to_abs(
                motor_value,
                deadzone=args.deadzone,
                invert=not args.no_invert_motor,
            )
            now = time.monotonic()
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
            print(
                f"\rseq={seq} abs_x={abs_x} abs_y={abs_y} pressed={pressed} valves={active or ['-']}      ",
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
            command_sock.close()
            state_sock.close()
            pygame.quit()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
