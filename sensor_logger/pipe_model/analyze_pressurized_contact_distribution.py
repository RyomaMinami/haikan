#!/usr/bin/env python3
"""Estimate wheel load sharing after the drive cylinder is pressurized.

This is a quasi-static penalty/contact model.  The cylinder is represented by
constant-force work, while passive and drive tires are linear radial springs.
The move body is allowed to translate in the pipe cross-section; body roll is
held at zero to match the insertion-case analysis.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
from scipy.optimize import minimize

import analyze_ridge_contact_cases as contact
from enumerate_gravity_contact_combinations import (
    WHEEL_NAMES,
    cached_com_model,
    com_at_angle,
)


DRIVE_NAMES = ["drive_1", "drive_2"]
CONTACT_FORCE_THRESHOLD_N = 2.0
DEFAULT_PRESSURE_MPA = 0.7
DEFAULT_BORE_MM = 32.0
DEFAULT_EFFICIENCY = 1.0
DEFAULT_DRIVE_STIFFNESS_N_PER_MM = 200.0
SENSITIVITY_STIFFNESSES_N_PER_MM = (100.0, 200.0, 400.0)
PRESS_BODY_CONTACT_STIFFNESS_N_PER_MM = 50000.0


def sampled(vertices: np.ndarray, fast_limit: int = 3200) -> np.ndarray:
    """Keep pipe-facing vertices plus a deterministic background sample."""
    if len(vertices) <= fast_limit:
        return vertices
    radial = np.linalg.norm(vertices[:, 1:3], axis=1)
    # Pipe interference can only occur on the radially exposed envelope.  The
    # same 104 mm cutoff used by the gravity solver is safer than a quantile:
    # a quantile can discard a small but critical protrusion.
    exposed = np.flatnonzero(radial >= 0.104)
    if len(exposed) > fast_limit - 300:
        exposed = exposed[
            np.linspace(0, len(exposed) - 1, fast_limit - 300, dtype=int)
        ]
    background = np.linspace(0, len(vertices) - 1, 300, dtype=int)
    return vertices[np.unique(np.r_[exposed, background])]


def inward_force(force_n: float, point: np.ndarray) -> np.ndarray:
    radial = float(np.linalg.norm(point[1:3]))
    if force_n <= 0.0 or radial == 0.0:
        return np.zeros(3)
    return np.array([
        0.0,
        -force_n * point[1] / radial,
        -force_n * point[2] / radial,
    ])


def cylinder_force_n(pressure_mpa: float, bore_mm: float, efficiency: float) -> float:
    return pressure_mpa * np.pi * bore_mm * bore_mm / 4.0 * efficiency


def solve_case(
    case: dict,
    estimation: dict,
    passive: list[np.ndarray],
    active_drive: list[np.ndarray],
    drilling_wheel: np.ndarray,
    fixed_body: np.ndarray,
    motor_body: np.ndarray,
    drilling_modules: dict[str, np.ndarray],
    pivot: np.ndarray,
    com_model: list[tuple[str, float, np.ndarray]],
    pressure_mpa: float,
    bore_mm: float,
    efficiency: float,
    drive_stiffness: float,
) -> dict:
    angle = float(case["rotation9_deg"])
    initial_pose = tuple(case["body_pose_m"][axis] for axis in ("x", "y", "z"))
    body_x = initial_pose[0]
    rotated_drilling_wheel = contact.rotate_about_pipe_axis(
        drilling_wheel, pivot, angle
    )
    rotated_modules = {
        name: contact.rotate_about_pipe_axis(vertices, pivot, angle)
        for name, vertices in drilling_modules.items()
    }
    static_bodies = {"move_module_body": fixed_body, **rotated_modules}
    static_samples = {name: sampled(vertices) for name, vertices in static_bodies.items()}
    motor_sample = sampled(motor_body)
    passive_wheels = passive + [rotated_drilling_wheel]
    base_com = com_at_angle(com_model, pivot, angle)
    motor_fraction = next(
        fraction for name, fraction, _ in com_model if name == "motor_module"
    )
    force_cylinder = cylinder_force_n(pressure_mpa, bore_mm, efficiency)

    first_stroke = case.get("cylinder_first_contact_stroke_mm")
    slider0 = (
        contact.SLIDER6_MIN_MM
        if first_stroke is None
        else contact.SLIDER6_MIN_MM + float(first_stroke)
    )

    def local_com(slider_mm: float) -> np.ndarray:
        result = base_com.copy()
        result[2] += motor_fraction * (
            slider_mm - contact.SLIDER6_MIN_MM
        ) * 0.001
        return result

    def gap(vertices: np.ndarray, y: float, z: float, slider_mm: float | None = None) -> float:
        return contact.clearance_mm(
            vertices,
            (body_x, y, z),
            estimation,
            slider_mm=(contact.SLIDER6_MIN_MM if slider_mm is None else slider_mm),
            body_roll_deg=0.0,
        )

    def potential(q: np.ndarray) -> float:
        y, z, slider_mm = map(float, q)
        passive_penetrations = [max(0.0, -gap(w, y, z)) for w in passive_wheels]
        drive_penetrations = [
            max(0.0, -gap(w, y, z, slider_mm)) for w in active_drive
        ]
        body_penetrations = [
            max(0.0, -gap(vertices, y, z))
            for vertices in static_samples.values()
        ]
        body_penetrations.append(max(0.0, -gap(motor_sample, y, z, slider_mm)))
        com_height_mm = (z + local_com(slider_mm)[2]) * 1000.0
        gravity = contact.ROBOT_MASS_KG * contact.GRAVITY_M_S2 * com_height_mm
        passive_energy = 0.5 * contact.WHEEL_RADIAL_STIFFNESS_N_PER_MM * sum(
            value * value for value in passive_penetrations
        )
        drive_energy = 0.5 * drive_stiffness * sum(
            value * value for value in drive_penetrations
        )
        body_energy = 0.5 * PRESS_BODY_CONTACT_STIFFNESS_N_PER_MM * sum(
            value * value for value in body_penetrations
        )
        actuator_work = -force_cylinder * (slider_mm - contact.SLIDER6_MIN_MM)
        return gravity + passive_energy + drive_energy + body_energy + actuator_work

    bounds = [
        (initial_pose[1] - 0.015, initial_pose[1] + 0.015),
        (initial_pose[2] - 0.025, initial_pose[2] + 0.015),
        (contact.SLIDER6_MIN_MM, contact.SLIDER6_MAX_MM),
    ]
    q = np.array([initial_pose[1], initial_pose[2], slider0])
    # Reinsert the exact worst full-mesh vertex after each solve.  This active-
    # set refinement prevents the optimization sample from missing a small
    # protrusion while retaining practical run time.
    for _ in range(2):
        result = minimize(
            potential,
            q,
            method="Powell",
            bounds=bounds,
            options={"maxiter": 320, "xtol": 1e-8, "ftol": 1e-8},
        )
        q = result.x
        y_refine, z_refine, slider_refine = map(float, q)
        pose_refine = (body_x, y_refine, z_refine)
        for name, vertices in static_bodies.items():
            points = contact.placed(vertices, *pose_refine)
            gaps = contact.pipe_radius_m(points, estimation) - np.linalg.norm(
                points[:, 1:3], axis=1
            )
            critical = vertices[int(np.argmin(gaps))][None, :]
            static_samples[name] = np.unique(
                np.vstack([static_samples[name], critical]), axis=0
            )
        motor_points = contact.placed(
            motor_body, *pose_refine, slider_mm=slider_refine
        )
        motor_gaps = contact.pipe_radius_m(
            motor_points, estimation
        ) - np.linalg.norm(motor_points[:, 1:3], axis=1)
        motor_sample = np.unique(
            np.vstack([
                motor_sample,
                motor_body[int(np.argmin(motor_gaps))][None, :],
            ]),
            axis=0,
        )
    body_y, body_z, slider_mm = map(float, result.x)
    pose = (body_x, body_y, body_z)

    def derivative(index: int, step: float) -> float:
        lower, upper = bounds[index]
        plus = result.x.copy()
        minus = result.x.copy()
        plus[index] = min(upper, plus[index] + step)
        minus[index] = max(lower, minus[index] - step)
        return (potential(plus) - potential(minus)) / (plus[index] - minus[index])

    # Potential is N mm.  y/z are in metres, therefore divide their
    # derivatives by 1000 to obtain generalized force in newtons.  Slider is
    # already expressed in millimetres.
    generalized_residual = {
        "body_y_n": float(derivative(0, 1e-6) / 1000.0),
        "body_z_n": float(derivative(1, 1e-6) / 1000.0),
        "slider_n": float(derivative(2, 1e-4)),
    }

    passive_details = [
        contact.clearance_details(wheel, pose, estimation)
        for wheel in passive_wheels
    ]
    drive_details = [
        contact.clearance_details(
            wheel, pose, estimation, slider_mm=slider_mm
        )
        for wheel in active_drive
    ]
    passive_forces = [
        contact.WHEEL_RADIAL_STIFFNESS_N_PER_MM * max(0.0, -item[0])
        for item in passive_details
    ]
    drive_forces = [
        drive_stiffness * max(0.0, -item[0]) for item in drive_details
    ]

    exact_body_details = {
        name: contact.clearance_details(vertices, pose, estimation)
        for name, vertices in static_bodies.items()
    }
    exact_body_details["motor_module_body"] = contact.clearance_details(
        motor_body, pose, estimation, slider_mm=slider_mm
    )
    body_forces = {
        name: PRESS_BODY_CONTACT_STIFFNESS_N_PER_MM * max(0.0, -item[0])
        for name, item in exact_body_details.items()
    }

    com_world = local_com(slider_mm) + np.asarray(pose)
    total_force = np.array([0.0, 0.0, -contact.ROBOT_MASS_KG * contact.GRAVITY_M_S2])
    torque_x = 0.0
    for force_n, (_, point) in zip(passive_forces, passive_details):
        vector = inward_force(force_n, point)
        total_force += vector
        torque_x += float(np.cross(point - com_world, vector)[0])
    drive_vectors = []
    for force_n, (_, point) in zip(drive_forces, drive_details):
        vector = inward_force(force_n, point)
        drive_vectors.append(vector)
        total_force += vector
        torque_x += float(np.cross(point - com_world, vector)[0])
    motor_contact_z = 0.0
    for name, force_n in body_forces.items():
        point = exact_body_details[name][1]
        vector = inward_force(force_n, point)
        total_force += vector
        torque_x += float(np.cross(point - com_world, vector)[0])
        if name == "motor_module_body":
            motor_contact_z += vector[2]

    cylinder_balance_error = force_cylinder + sum(v[2] for v in drive_vectors) + motor_contact_z
    passive_contact = [force >= CONTACT_FORCE_THRESHOLD_N for force in passive_forces]
    drive_contact = [force >= CONTACT_FORCE_THRESHOLD_N for force in drive_forces]
    minimum_body_clearance = min(item[0] for item in exact_body_details.values())
    body_contact_ids = [
        name for name, force_n in body_forces.items()
        if force_n >= CONTACT_FORCE_THRESHOLD_N
    ]
    at_stroke_limit = slider_mm >= contact.SLIDER6_MAX_MM - 1e-4
    valid = bool(
        minimum_body_clearance >= -0.02
        and abs(generalized_residual["body_y_n"]) <= 5.0
        and abs(generalized_residual["body_z_n"]) <= 5.0
        and (at_stroke_limit or abs(generalized_residual["slider_n"]) <= 5.0)
    )
    return {
        "case_label": case["case_label"],
        "rotation9_deg": angle,
        "axial_phase_mm": float(case["axial_phase_mm"]),
        "solver_success": bool(result.success),
        "solver_message": str(result.message),
        "valid": valid,
        "body_pose_m": {"x": body_x, "y": body_y, "z": body_z},
        "body_roll_deg": 0.0,
        "slider6_mm": slider_mm,
        "cylinder_stroke_from_min_mm": slider_mm - contact.SLIDER6_MIN_MM,
        "at_extension_limit": at_stroke_limit,
        "cylinder_force_n": force_cylinder,
        "cylinder_axis_balance_error_n": cylinder_balance_error,
        "passive_wheel_ids": WHEEL_NAMES,
        "passive_normal_force_n": passive_forces,
        "passive_contact": passive_contact,
        "floating_passive_wheels": [
            name for name, flag in zip(WHEEL_NAMES, passive_contact) if not flag
        ],
        "drive_wheel_ids": DRIVE_NAMES,
        "drive_normal_force_n": drive_forces,
        "drive_contact": drive_contact,
        "floating_drive_wheels": [
            name for name, flag in zip(DRIVE_NAMES, drive_contact) if not flag
        ],
        "passive_clearance_mm": [item[0] for item in passive_details],
        "drive_clearance_mm": [item[0] for item in drive_details],
        "body_clearance_mm": {
            name: item[0] for name, item in exact_body_details.items()
        },
        "body_contact_ids": body_contact_ids,
        "body_contact_force_n": body_forces,
        "minimum_body_clearance_mm": minimum_body_clearance,
        "whole_robot_force_residual_n": {
            "y": float(total_force[1]), "z": float(total_force[2])
        },
        "generalized_equilibrium_residual_n": generalized_residual,
        "required_roll_holding_torque_n_m": float(-torque_x),
        "drive_stiffness_n_per_mm": drive_stiffness,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pressure-mpa", type=float, default=DEFAULT_PRESSURE_MPA)
    parser.add_argument("--bore-mm", type=float, default=DEFAULT_BORE_MM)
    parser.add_argument("--efficiency", type=float, default=DEFAULT_EFFICIENCY)
    parser.add_argument("--stiffness", type=float, nargs="+")
    parser.add_argument("--max-cases", type=int)
    parser.add_argument(
        "--output-stem",
        default="pressurized_contact_distribution_0p7mpa",
        help="Output filename stem (without .json/.csv).",
    )
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    output = here / "output"
    cases_data = json.loads(
        (output / "initial_module_orientation_validated.json").read_text(encoding="utf-8")
    )
    estimation = json.loads(
        (output / "helical_ridge_estimation.json").read_text(encoding="utf-8")
    )
    previous = json.loads(
        (output / "ridge_contact_cases.json").read_text(encoding="utf-8")
    )
    assets = here.parent.parent / "raspi_tools" / "camera_stream" / "assets"
    passive, drive, drilling, drilling_modules = contact.load_geometry(assets)
    fixed = contact.load_fixed_body_geometry(assets)
    active_drive = [drive[index] for index in contact.ACTIVE_DRIVE_WHEEL_INDICES]

    base = contact.load_robot_mesh(assets / "01_move_module.stl")
    motor = contact.load_robot_mesh(assets / "02_motor_module.stl")
    motor_offset = contact.ROBOT_MODULE_POSITION_MM + np.array(
        [0.0, contact.SLIDER6_MIN_MM, 0.0]
    )
    cad_center = (
        np.minimum(base.bounds[0], motor.bounds[0] + motor_offset)
        + np.maximum(base.bounds[1], motor.bounds[1] + motor_offset)
    ) * 0.5
    pivot = contact.cad_to_sim(contact.ROTATION9_PIVOT_CAD_MM - cad_center)
    com_model = cached_com_model(previous["cases"][0])

    all_scenarios: dict[str, list[dict]] = {}
    stiffness_values = tuple(args.stiffness or SENSITIVITY_STIFFNESSES_N_PER_MM)
    selected_cases = cases_data["simulator_cases"][:args.max_cases]
    for stiffness in stiffness_values:
        print(f"[STIFFNESS] {stiffness:.0f} N/mm", flush=True)
        rows = []
        for index, case in enumerate(selected_cases, 1):
            row = solve_case(
                case, estimation, passive, active_drive, drilling[0],
                fixed["move_module_body"], fixed["motor_module_body_retracted"],
                drilling_modules, pivot, com_model,
                args.pressure_mpa, args.bore_mm, args.efficiency, stiffness,
            )
            rows.append(row)
            print(
                f"  {index:02d}: passive="
                f"{sum(row['passive_contact'])}/5 drive={sum(row['drive_contact'])}/2 "
                f"stroke={row['cylinder_stroke_from_min_mm']:.2f} mm "
                f"gap={row['minimum_body_clearance_mm']:+.3f} mm "
                f"valid={row['valid']}",
                flush=True,
            )
        all_scenarios[f"{stiffness:g}"] = rows

    primary_key = (
        f"{DEFAULT_DRIVE_STIFFNESS_N_PER_MM:g}"
        if f"{DEFAULT_DRIVE_STIFFNESS_N_PER_MM:g}" in all_scenarios
        else next(iter(all_scenarios))
    )
    primary = all_scenarios[primary_key]
    result = {
        "method": "quasi-static constant-force cylinder plus linear radial wheel springs",
        "pressure_mpa": args.pressure_mpa,
        "cylinder_bore_mm": args.bore_mm,
        "cylinder_efficiency": args.efficiency,
        "theoretical_cylinder_force_n": cylinder_force_n(
            args.pressure_mpa, args.bore_mm, args.efficiency
        ),
        "passive_stiffness_n_per_mm": contact.WHEEL_RADIAL_STIFFNESS_N_PER_MM,
        "body_penalty_stiffness_n_per_mm": PRESS_BODY_CONTACT_STIFFNESS_N_PER_MM,
        "primary_drive_stiffness_n_per_mm": float(primary_key),
        "contact_force_threshold_n": CONTACT_FORCE_THRESHOLD_N,
        "important_limitations": [
            "Wheel stiffness is assumed, not measured.",
            "Body roll is constrained to zero and its holding torque is reported.",
            "Contact is concentrated at the minimum-clearance STL vertex.",
            "Reported radial force components approximate the helical surface normal; generalized residuals from the potential gradient are used for convergence.",
            "Cylinder friction, guide friction, tire hysteresis and distributed contact are omitted.",
        ],
        "primary_cases": primary,
        "stiffness_sensitivity": all_scenarios,
    }
    json_path = output / f"{args.output_stem}.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    csv_path = output / f"{args.output_stem}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "case", "rotation9_deg", "phase_mm", "valid", "stroke_mm",
            "passive_contacts", "floating_passive", "drive_contacts",
            "passive_force_n", "drive_force_n", "min_body_gap_mm",
            "force_residual_y_n", "force_residual_z_n", "hold_torque_nm",
        ])
        for index, row in enumerate(primary, 11):
            writer.writerow([
                index, row["rotation9_deg"], row["axial_phase_mm"], row["valid"],
                row["cylinder_stroke_from_min_mm"],
                "+".join(name for name, flag in zip(WHEEL_NAMES, row["passive_contact"]) if flag),
                "+".join(row["floating_passive_wheels"]),
                "+".join(name for name, flag in zip(DRIVE_NAMES, row["drive_contact"]) if flag),
                ";".join(f"{value:.3f}" for value in row["passive_normal_force_n"]),
                ";".join(f"{value:.3f}" for value in row["drive_normal_force_n"]),
                row["minimum_body_clearance_mm"],
                row["whole_robot_force_residual_n"]["y"],
                row["whole_robot_force_residual_n"]["z"],
                row["required_roll_holding_torque_n_m"],
            ])
    print(f"output={json_path}")
    print(f"output={csv_path}")


if __name__ == "__main__":
    main()
