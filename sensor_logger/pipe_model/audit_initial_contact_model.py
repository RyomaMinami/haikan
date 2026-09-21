#!/usr/bin/env python3
"""Audit best full-mesh insertion state at each sampled rotation9 angle."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import analyze_ridge_contact_cases as contact
from enumerate_gravity_contact_combinations import cached_com_model, com_at_angle


def main() -> None:
    here = Path(__file__).resolve().parent
    output = here / "output"
    coarse = json.loads(
        (output / "initial_module_orientation_contacts.json").read_text(encoding="utf-8")
    )
    estimation = json.loads(
        (output / "helical_ridge_estimation.json").read_text(encoding="utf-8")
    )
    previous = json.loads(
        (output / "ridge_contact_cases.json").read_text(encoding="utf-8")
    )
    assets = here.parent.parent / "raspi_tools" / "camera_stream" / "assets"
    passive, _, drilling, modules = contact.load_geometry(assets)
    fixed_bodies = contact.load_fixed_body_geometry(assets)
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
    rows = []
    for angle in coarse["initial_rotation9_angles_deg"]:
        candidates = [
            row for row in coarse["states"]
            if row["initial_rotation9_deg"] == angle
        ]
        seed = max(candidates, key=lambda row: row["minimum_body_clearance_mm"])
        rotated_wheels = [
            contact.rotate_about_pipe_axis(wheel, pivot, angle) for wheel in drilling
        ]
        rotated_modules = {
            name: contact.rotate_about_pipe_axis(vertices, pivot, angle)
            for name, vertices in modules.items()
        }
        collision_bodies = {**fixed_bodies, **rotated_modules}
        pose0 = tuple(seed["body_pose_m"][axis] for axis in ("x", "y", "z"))
        pose, roll, settled = contact.settle_under_gravity(
            passive, rotated_wheels, collision_bodies, pose0, 0.0,
            com_at_angle(com_model, pivot, angle), estimation,
            fast=False, fixed_body_roll_deg=0.0,
        )
        body_gap = min(settled["body_clearance_mm"].values())
        force_error = abs(settled["vertical_support_force_n"] - settled["weight_n"])
        valid = bool(
            settled["success"] and body_gap >= -0.005 and force_error <= 5.0
            and abs(settled["lateral_force_residual_n"]) <= 5.0
        )
        rows.append({
            "initial_rotation9_deg": angle,
            "axial_phase_mm": seed["axial_phase_mm"],
            "body_pose_m": {"x": pose[0], "y": pose[1], "z": pose[2]},
            "body_roll_deg": roll,
            "minimum_body_clearance_mm": body_gap,
            "vertical_force_balance_error_n": force_error,
            "lateral_force_residual_n": settled["lateral_force_residual_n"],
            "required_body_roll_holding_torque_n_m": settled[
                "roll_constraint_torque_n_m"
            ],
            "valid": valid,
        })
        print(
            f"{angle:+.0f} deg: gap={body_gap:+.3f} mm, "
            f"Fz_err={force_error:.3f} N, "
            f"Fy={settled['lateral_force_residual_n']:+.3f} N, "
            f"hold={settled['roll_constraint_torque_n_m']:+.3f} Nm, valid={valid}"
        )
    valid_angles = [row["initial_rotation9_deg"] for row in rows if row["valid"]]
    result = {
        "method": "full-mesh validation of the maximum-clearance coarse state at every 5 degree sample",
        "validity": "optimizer success, body clearance >= -0.005 mm, vertical and lateral force errors <= 5 N",
        "angle_step_deg": 5.0,
        "valid_angle_samples_deg": valid_angles,
        "validated_contiguous_sample_range_deg": (
            [min(valid_angles), max(valid_angles)] if valid_angles else None
        ),
        "angles": rows,
    }
    path = output / "initial_contact_model_audit.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"output={path}")


if __name__ == "__main__":
    main()
