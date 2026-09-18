#!/usr/bin/env python3
"""Enumerate passive-wheel-on-ridge cases and first drive-wheel contact."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from scipy.optimize import brentq

from run_measured_pipe_simulator import (
    BIT_MODULE_POSITION_MM,
    DRIVE_WHEELS_MODULE_CAD_MM,
    DRILLING1_MODULE_POSITION_MM,
    DRILLING2_MODULE_POSITION_MM,
    DRILLING_WHEELS_MODULE_CAD_MM,
    NORMAL_PASSIVE_SEAT_Y_M,
    PASSIVE_WHEELS_CAD_MM,
    ROBOT_MODULE_POSITION_MM,
    SLIDER6_MAX_MM,
    SLIDER6_MIN_MM,
    SLIDER11_MIN_MM,
    cad_to_sim,
    drive_wheel_components,
    drilling_wheel_components,
    load_robot_mesh,
    passive_wheel_components,
)


# Use a tight tolerance for geometric contact.  The previous 0.25 mm display
# tolerance incorrectly marked nearby drive wheels as touching even though a
# positive gap remained at the rigid first-contact cylinder position.
CONTACT_TOLERANCE_MM = 0.02


def load_geometry(
    asset_dir: Path,
) -> tuple[
    list[np.ndarray], list[np.ndarray], list[np.ndarray], dict[str, np.ndarray]
]:
    base = load_robot_mesh(asset_dir / "01_move_module.stl")
    motor = load_robot_mesh(asset_dir / "02_motor_module.stl")
    drilling1 = load_robot_mesh(asset_dir / "03_drilling_module1.stl")
    drilling2 = load_robot_mesh(asset_dir / "03_drilling_module2.stl")
    bit = load_robot_mesh(asset_dir / "04_bit.stl")
    motor_offset = ROBOT_MODULE_POSITION_MM + np.array([0.0, SLIDER6_MIN_MM, 0.0])
    minimum = np.minimum(base.bounds[0], motor.bounds[0] + motor_offset)
    maximum = np.maximum(base.bounds[1], motor.bounds[1] + motor_offset)
    cad_center = (minimum + maximum) * 0.5
    passive = [
        cad_to_sim(wheel.convex_hull.vertices - cad_center)
        for wheel in passive_wheel_components(base)
    ]
    drive = [
        cad_to_sim(wheel.convex_hull.vertices + motor_offset - cad_center)
        for wheel in drive_wheel_components(motor)
    ]
    drilling = [
        cad_to_sim(
            wheel.convex_hull.vertices
            + DRILLING1_MODULE_POSITION_MM - cad_center
        )
        for wheel in drilling_wheel_components(drilling1)
    ]
    bit_rotation = np.array([
        [1.0, 0.0, 0.0],
        [0.0, 0.0, -1.0],
        [0.0, 1.0, 0.0],
    ])
    drilling_modules = {
        "drilling_module1": cad_to_sim(
            np.asarray(drilling1.vertices)
            + DRILLING1_MODULE_POSITION_MM - cad_center
        ),
        "drilling_module2": cad_to_sim(
            np.asarray(drilling2.vertices)
            + DRILLING2_MODULE_POSITION_MM
            + np.array([0.0, SLIDER11_MIN_MM, 0.0]) - cad_center
        ),
        "bit": cad_to_sim(
            np.asarray(bit.vertices) @ bit_rotation.T
            + BIT_MODULE_POSITION_MM
            + np.array([0.0, SLIDER11_MIN_MM, 0.0]) - cad_center
        ),
    }
    return passive, drive, drilling, drilling_modules


def pipe_radius_m(points: np.ndarray, estimation: dict) -> np.ndarray:
    x_mm = points[:, 0] * 1000.0
    theta_deg = np.degrees(np.arctan2(points[:, 2], points[:, 1]))
    lead = float(estimation["lead_mm_per_revolution"])
    profile = estimation["profile"]
    start = float(profile["crest_phase_at_theta0_mm"])
    distance = np.abs(
        np.mod(x_mm - start - lead * theta_deg / 360.0 + 0.5 * lead, lead)
        - 0.5 * lead
    )
    distance_knots = np.array([
        0.0,
        0.5 * float(profile["crest_width_90pct_height_mm"]),
        0.5 * float(profile["crest_width_80pct_height_mm"]),
        0.5 * float(profile["half_height_width_mm"]),
        0.5 * lead,
    ])
    height = float(profile["peak_to_valley_mm"])
    height_knots = height * np.array([1.0, 0.9, 0.8, 0.5, 0.0])
    depth_mm = np.interp(distance, distance_knots, height_knots)
    return (120.0 - depth_mm) * 0.001


def placed(vertices: np.ndarray, body_x: float, body_y: float,
           body_z: float, slider_mm: float = SLIDER6_MIN_MM) -> np.ndarray:
    points = vertices.copy()
    points[:, 2] += (slider_mm - SLIDER6_MIN_MM) * 0.001
    points += np.array([body_x, body_y, body_z])
    return points


def clearance_mm(vertices: np.ndarray, pose: tuple[float, float, float],
                 estimation: dict, slider_mm: float = SLIDER6_MIN_MM) -> float:
    points = placed(vertices, *pose, slider_mm)
    radial = np.linalg.norm(points[:, 1:3], axis=1)
    return float(np.min(pipe_radius_m(points, estimation) - radial) * 1000.0)


def clearance_details(
    vertices: np.ndarray,
    pose: tuple[float, float, float],
    estimation: dict,
) -> tuple[float, np.ndarray]:
    points = placed(vertices, *pose)
    radial = np.linalg.norm(points[:, 1:3], axis=1)
    gaps = pipe_radius_m(points, estimation) - radial
    index = int(np.argmin(gaps))
    return float(gaps[index] * 1000.0), points[index]


def solve_zero(function, lower: float, upper: float) -> float:
    samples = np.linspace(lower, upper, 161)
    values = [function(value) for value in samples]
    roots = [
        brentq(function, samples[index], samples[index + 1])
        for index in range(len(samples) - 1)
        if values[index] == 0 or values[index] * values[index + 1] < 0
    ]
    if not roots:
        raise RuntimeError("No contact root was found in the permitted range")
    return min(roots, key=abs)


def align_selected_wheel(selected: np.ndarray, estimation: dict,
                         pipe_center_x: float) -> tuple[float, float]:
    """Align the selected passive wheel's lowest point with a helix crest."""
    body_y = NORMAL_PASSIVE_SEAT_Y_M
    body_z = 0.0
    lead_m = float(estimation["lead_mm_per_revolution"]) * 0.001
    start_m = float(estimation["profile"]["crest_phase_at_theta0_mm"]) * 0.001
    body_x = pipe_center_x
    for _ in range(4):
        provisional = placed(selected, body_x, body_y, body_z)
        radial = np.linalg.norm(provisional[:, 1:3], axis=1)
        index = int(np.argmax(radial))
        theta_turn = np.arctan2(provisional[index, 2], provisional[index, 1]) / (2 * np.pi)
        local_x = selected[index, 0]
        turn = round((pipe_center_x + local_x - start_m - lead_m * theta_turn) / lead_m)
        body_x = start_m + turn * lead_m + lead_m * theta_turn - local_x
        body_z = solve_zero(
            lambda z: clearance_mm(
                selected, (body_x, body_y, z), estimation
            ),
            -0.040, 0.040,
        )
    return body_x, body_z


def align_wheel_group(selected: list[np.ndarray], estimation: dict,
                      pipe_center_x: float) -> tuple[float, float]:
    """Place a group at the mean of their individual helical crest phases."""
    individual = [
        align_selected_wheel(wheel, estimation, pipe_center_x)
        for wheel in selected
    ]
    body_x = float(np.mean([pose[0] for pose in individual]))
    body_z_guess = float(np.mean([pose[1] for pose in individual]))
    body_z = solve_zero(
        lambda z: min(
            clearance_mm(
                wheel, (body_x, NORMAL_PASSIVE_SEAT_Y_M, z), estimation
            )
            for wheel in selected
        ),
        body_z_guess - 0.010,
        body_z_guess + 0.010,
    )
    return body_x, body_z


def drive_contact_stroke(vertices: np.ndarray,
                         pose: tuple[float, float, float],
                         estimation: dict) -> float | None:
    minimum = clearance_mm(vertices, pose, estimation, SLIDER6_MIN_MM)
    maximum = clearance_mm(vertices, pose, estimation, SLIDER6_MAX_MM)
    if minimum <= 0.0:
        return 0.0
    if maximum > 0.0:
        return None
    return brentq(
        lambda stroke: clearance_mm(
            vertices, pose, estimation, SLIDER6_MIN_MM + stroke
        ),
        0.0,
        SLIDER6_MAX_MM - SLIDER6_MIN_MM,
    )


def main() -> None:
    here = Path(__file__).resolve().parent
    estimation_path = here / "output" / "helical_ridge_estimation.json"
    estimation = json.loads(estimation_path.read_text(encoding="utf-8"))
    assets = here.parent.parent / "raspi_tools" / "camera_stream" / "assets"
    passive, drive, drilling, drilling_modules = load_geometry(assets)
    pipe_metadata_path = here / "output" / "pipe_full360_helix_estimated.json"
    if pipe_metadata_path.exists():
        pipe_metadata = json.loads(pipe_metadata_path.read_text(encoding="utf-8"))
        x_min, x_max = (
            value * 0.001 for value in pipe_metadata["axial_range_mm"]
        )
    else:
        x_min, x_max = (
            value * 0.001 for value in estimation["measured_range"]["x_mm"]
        )
    pipe_center_x = 0.5 * (x_min + x_max)
    # The inferred right-hand helix puts wheels 2 and 3 on the same crest:
    # their individual crest phases differ by only about 2 mm, versus a
    # measured 25.9 mm-wide 90%-height crest.  They are therefore one physical
    # case rather than two duplicate cases.
    wheel_groups = [[0], [1, 2], [3]]
    cases = []
    for case_index, selected_indices in enumerate(wheel_groups, start=1):
        selected = [passive[index] for index in selected_indices]
        body_x, body_z = align_wheel_group(selected, estimation, pipe_center_x)
        pose = (body_x, NORMAL_PASSIVE_SEAT_Y_M, body_z)
        passive_clearances = [
            clearance_mm(wheel, pose, estimation) for wheel in passive
        ]
        drilling_clearances = [
            clearance_mm(wheel, pose, estimation) for wheel in drilling
        ]
        module_details = {
            name: clearance_details(vertices, pose, estimation)
            for name, vertices in drilling_modules.items()
        }
        closest_module, (module_gap, module_point) = min(
            module_details.items(), key=lambda item: item[1][0]
        )
        strokes = [drive_contact_stroke(wheel, pose, estimation) for wheel in drive]
        reachable = [value for value in strokes if value is not None]
        stop_stroke = min(reachable) if reachable else None
        if stop_stroke is None:
            drive_clearances = [
                clearance_mm(wheel, pose, estimation, SLIDER6_MAX_MM)
                for wheel in drive
            ]
            drive_contact = [False] * len(drive)
        else:
            slider = SLIDER6_MIN_MM + stop_stroke
            drive_clearances = [
                clearance_mm(wheel, pose, estimation, slider) for wheel in drive
            ]
            drive_contact = [gap <= CONTACT_TOLERANCE_MM for gap in drive_clearances]
        cases.append({
            "case": case_index,
            "passive_wheels_on_ridge": [index + 1 for index in selected_indices],
            "passive_wheel_cad_centers_mm": [
                PASSIVE_WHEELS_CAD_MM[index].tolist()
                for index in selected_indices
            ],
            "body_pose_m": {"x": body_x, "y": NORMAL_PASSIVE_SEAT_Y_M, "z": body_z},
            "passive_clearance_mm": passive_clearances,
            "passive_contact": [
                gap <= CONTACT_TOLERANCE_MM for gap in passive_clearances
            ],
            "drilling_wheel_clearance_mm": drilling_clearances,
            "drilling_contact": [
                gap <= CONTACT_TOLERANCE_MM for gap in drilling_clearances
            ],
            "drilling_module_after_press": {
                "sequence": "checked after drive-wheel first contact",
                "contact": module_gap <= CONTACT_TOLERANCE_MM,
                "minimum_clearance_mm": module_gap,
                "closest_component": closest_module,
                "closest_point_world_m": module_point.tolist(),
                "component_clearance_mm": {
                    name: detail[0] for name, detail in module_details.items()
                },
            },
            "drive_required_stroke_mm": strokes,
            "cylinder_first_contact_stroke_mm": stop_stroke,
            "drive_clearance_at_first_contact_mm": drive_clearances,
            "drive_contact": drive_contact,
        })
    result = {
        "method": "fixed normal pose; distinct passive-wheel groups aligned to inferred helix crest; rigid first-contact analysis",
        "contact_tolerance_mm": CONTACT_TOLERANCE_MM,
        "maximum_cylinder_stroke_mm": SLIDER6_MAX_MM - SLIDER6_MIN_MM,
        "passive_wheel_order_cad_centers_mm": PASSIVE_WHEELS_CAD_MM.tolist(),
        "drive_wheel_order_cad_centers_mm": DRIVE_WHEELS_MODULE_CAD_MM.tolist(),
        "drilling_wheel_order_cad_centers_mm": DRILLING_WHEELS_MODULE_CAD_MM.tolist(),
        "cases": cases,
    }
    output = here / "output" / "ridge_contact_cases.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"output={output}")


if __name__ == "__main__":
    main()
