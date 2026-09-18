#!/usr/bin/env python3
"""Validate one representative of each coarse passive-wheel combination."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import analyze_ridge_contact_cases as contact
from enumerate_gravity_contact_combinations import (
    WHEEL_NAMES,
    cached_com_model,
    com_at_angle,
)


def main() -> None:
    here = Path(__file__).resolve().parent
    output = here / "output"
    sweep = json.loads(
        (output / "gravity_passive_contact_combinations.json").read_text(encoding="utf-8")
    )
    estimation = json.loads(
        (output / "helical_ridge_estimation.json").read_text(encoding="utf-8")
    )
    base_cases = json.loads(
        (output / "ridge_contact_cases.json").read_text(encoding="utf-8")
    )
    assets = here.parent.parent / "raspi_tools" / "camera_stream" / "assets"
    passive, drive, drilling, drilling_modules = contact.load_geometry(assets)
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
    com_model = cached_com_model(base_cases["cases"][0])

    validated = []
    for index, candidate in enumerate(sweep["combinations"], start=1):
        coarse_key = candidate["ridge_support_combination"]
        angle = float(candidate["sample_rotation9_deg"])
        phase = float(candidate["sample_axial_phase_mm"])
        coarse = next(
            row for row in sweep["states"]
            if row["ridge_support_combination"] == coarse_key
            and row["rotation9_deg"] == angle
            and row["axial_phase_mm"] == phase
        )
        print(
            f"[VALIDATE {index:02d}/{len(sweep['combinations']):02d}] "
            f"{coarse_key} angle={angle:+.1f} phase={phase:.2f}",
            flush=True,
        )
        rotated_drilling = [
            contact.rotate_about_pipe_axis(wheel, pivot, angle) for wheel in drilling
        ]
        rotated_modules = {
            name: contact.rotate_about_pipe_axis(vertices, pivot, angle)
            for name, vertices in drilling_modules.items()
        }
        com = com_at_angle(com_model, pivot, angle)
        initial_pose = (
            float(coarse["body_x_m"]),
            float(coarse["body_y_m"]),
            float(coarse["body_z_m"]),
        )
        pose, roll, settled = contact.settle_under_gravity(
            passive,
            rotated_drilling,
            rotated_modules,
            initial_pose,
            float(coarse["body_roll_deg"]),
            com,
            estimation,
            fast=False,
        )
        wheel_sets = passive + rotated_drilling
        details = [
            contact.clearance_details(wheel, pose, estimation, roll)
            for wheel in wheel_sets
        ]
        heights = [
            contact.local_ridge_height_mm(point, estimation) for _, point in details
        ]
        support_ids = [
            WHEEL_NAMES[i] for i, force in enumerate(settled["wheel_normal_force_n"])
            if force >= 2.0
        ]
        ridge_ids = [
            WHEEL_NAMES[i] for i, force in enumerate(settled["wheel_normal_force_n"])
            if force >= 2.0 and heights[i] >= contact.RIDGE_MIN_HEIGHT_MM
        ]
        minimum_body_gap = min(settled["body_clearance_mm"].values())
        force_error = abs(
            settled["vertical_support_force_n"] - settled["weight_n"]
        )
        physically_valid = (
            bool(settled["success"])
            and minimum_body_gap >= -contact.CONTACT_TOLERANCE_MM
            and force_error <= 5.0
        )
        validated.append({
            "coarse_ridge_support_combination": coarse_key,
            "validated_support_combination": "+".join(support_ids) or "none",
            "validated_ridge_support_combination": "+".join(ridge_ids) or "none",
            "rotation9_deg": angle,
            "axial_phase_mm": phase,
            "body_pose_m": {"x": pose[0], "y": pose[1], "z": pose[2]},
            "body_roll_deg": roll,
            "wheel_clearance_mm": [detail[0] for detail in details],
            "wheel_normal_force_n": settled["wheel_normal_force_n"],
            "wheel_ridge_height_mm": heights,
            "body_clearance_mm": settled["body_clearance_mm"],
            "minimum_body_clearance_mm": minimum_body_gap,
            "body_contact_within_tolerance": minimum_body_gap >= -contact.CONTACT_TOLERANCE_MM,
            "vertical_force_balance_error_n": force_error,
            "physically_valid": physically_valid,
            "vertical_support_force_n": settled["vertical_support_force_n"],
            "weight_n": settled["weight_n"],
            "solver_success": settled["success"],
        })

    unique = {}
    for row in validated:
        if not row["physically_valid"]:
            continue
        key = row["validated_ridge_support_combination"]
        existing = unique.get(key)
        if existing is None or row["minimum_body_clearance_mm"] > existing["minimum_body_clearance_mm"]:
            unique[key] = row
    simulator_cases = []
    for row in unique.values():
        angle = float(row["rotation9_deg"])
        pose = tuple(row["body_pose_m"][key] for key in ("x", "y", "z"))
        roll = float(row["body_roll_deg"])
        rotated_modules = {
            name: contact.rotate_about_pipe_axis(vertices, pivot, angle)
            for name, vertices in drilling_modules.items()
        }
        module_details = {
            name: contact.clearance_details(vertices, pose, estimation, roll)
            for name, vertices in rotated_modules.items()
        }
        closest_name, (module_gap, module_point) = min(
            module_details.items(), key=lambda item: item[1][0]
        )
        strokes = [
            contact.drive_contact_stroke(wheel, pose, estimation, roll)
            for wheel in drive
        ]
        reachable = [value for value in strokes if value is not None]
        stop_stroke = min(reachable) if reachable else None
        slider = (
            contact.SLIDER6_MAX_MM if stop_stroke is None
            else contact.SLIDER6_MIN_MM + stop_stroke
        )
        drive_gaps = [
            contact.clearance_mm(wheel, pose, estimation, slider, roll)
            for wheel in drive
        ]
        support_flags = [force >= 2.0 for force in row["wheel_normal_force_n"]]
        ridge_platform = [
            index + 1 for index in range(4)
            if support_flags[index]
            and row["wheel_ridge_height_mm"][index] >= contact.RIDGE_MIN_HEIGHT_MM
        ]
        ridge_key = row["validated_ridge_support_combination"]
        simulator_cases.append({
            "case_label": f"gravity sweep: ridge supports {ridge_key}",
            "source": "gravity_passive_contact_representatives",
            "rotation9_deg": angle,
            "axial_phase_mm": row["axial_phase_mm"],
            "valid_configuration": True,
            "diagnostic_reason": "high-accuracy gravity equilibrium",
            "passive_wheels_on_ridge": ridge_platform,
            "body_pose_m": row["body_pose_m"],
            "body_roll_deg": roll,
            "passive_clearance_mm": row["wheel_clearance_mm"][:4],
            "passive_contact": support_flags[:4],
            "drilling_wheel_clearance_mm": [row["wheel_clearance_mm"][4]],
            "drilling_contact": [support_flags[4]],
            "gravity_settled": {
                "success": row["solver_success"],
                "sequence": "all passive wheels enabled before cylinder press",
                "mass_kg": contact.ROBOT_MASS_KG,
                "gravity_m_s2": contact.GRAVITY_M_S2,
                "com_method": "user-approved provisional STL enclosed-volume ratio",
                "contact_wheels": [
                    index + 1 for index, enabled in enumerate(support_flags) if enabled
                ],
                "wheel_clearance_mm": row["wheel_clearance_mm"],
                "wheel_normal_force_n": row["wheel_normal_force_n"],
                "body_clearance_mm": row["body_clearance_mm"],
                "vertical_support_force_n": row["vertical_support_force_n"],
                "weight_n": row["weight_n"],
            },
            "drilling_module_after_press": {
                "sequence": "gravity settlement, then drive-wheel first contact",
                "contact": module_gap <= contact.CONTACT_TOLERANCE_MM,
                "intended_drilling_wheel_contact": support_flags[4],
                "forbidden_penetration": module_gap < -contact.CONTACT_TOLERANCE_MM,
                "minimum_clearance_mm": module_gap,
                "closest_component": closest_name,
                "closest_point_world_m": module_point.tolist(),
                "component_clearance_mm": {
                    name: detail[0] for name, detail in module_details.items()
                },
            },
            "drive_required_stroke_mm": strokes,
            "cylinder_first_contact_stroke_mm": stop_stroke,
            "drive_clearance_at_first_contact_mm": drive_gaps,
            "drive_contact": [
                gap <= contact.CONTACT_TOLERANCE_MM for gap in drive_gaps
            ],
        })

    result = {
        "method": "full-surface high-accuracy gravity re-settlement of coarse representatives",
        "contact_tolerance_mm": contact.CONTACT_TOLERANCE_MM,
        "wheel_ids": sweep["wheel_ids"],
        "validated_candidates": validated,
        "unique_representatives": list(unique.values()),
        "simulator_cases": simulator_cases,
    }
    path = output / "gravity_passive_contact_representatives.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["unique_representatives"], ensure_ascii=False, indent=2))
    print(f"output={path}")


if __name__ == "__main__":
    main()
