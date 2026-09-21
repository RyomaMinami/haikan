#!/usr/bin/env python3
"""Compare passive contacts at straight and pre-rotated drilling-module insertion."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import analyze_ridge_contact_cases as contact
from enumerate_gravity_contact_combinations import (
    WHEEL_NAMES,
    cached_com_model,
    com_at_angle,
)


def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    for row in rows:
        if not row["valid_without_body_penetration"]:
            continue
        key = (
            row["module_orientation_group"],
            row["support_combination"],
            row["ridge_support_combination"],
        )
        group = groups.setdefault(str(key), {
            "orientation_group": key[0],
            "support_combination": key[1],
            "ridge_support_combination": key[2],
            "count": 0,
            "angles_deg": set(),
            "representative": None,
        })
        group["count"] += 1
        group["angles_deg"].add(row["initial_rotation9_deg"])
        old = group["representative"]
        if old is None or row["minimum_body_clearance_mm"] > old["minimum_body_clearance_mm"]:
            group["representative"] = row
    result = list(groups.values())
    for group in result:
        group["angles_deg"] = sorted(group["angles_deg"])
        group["representative"] = {
            key: group["representative"][key]
            for key in (
                "initial_rotation9_deg", "axial_phase_mm", "body_pose_m",
                "support_ids", "ridge_support_ids", "drive_contact",
                "cylinder_first_contact_stroke_mm", "minimum_body_clearance_mm",
            )
        }
    return sorted(result, key=lambda row: (row["orientation_group"], -row["count"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axial-step-mm", type=float, default=20.0)
    parser.add_argument("--angle-step-deg", type=float, default=30.0)
    parser.add_argument(
        "--reuse-states", action="store_true",
        help="Rebuild groups from the existing JSON states without rerunning physics",
    )
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    output = here / "output"
    path = output / "initial_module_orientation_contacts.json"
    if args.reuse_states:
        result = json.loads(path.read_text(encoding="utf-8"))
        result["groups"] = summarize(result["states"])
        path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"reused={len(result['states'])} groups={len(result['groups'])}")
        return
    estimation = json.loads(
        (output / "helical_ridge_estimation.json").read_text(encoding="utf-8")
    )
    previous = json.loads(
        (output / "ridge_contact_cases.json").read_text(encoding="utf-8")
    )
    assets = here.parent.parent / "raspi_tools" / "camera_stream" / "assets"
    passive, drive, drilling, drilling_modules = contact.load_geometry(assets)
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
    lead_mm = float(estimation["lead_mm_per_revolution"])
    count = max(2, int(np.ceil(lead_mm / args.axial_step_mm)))
    phases = np.linspace(0.0, lead_mm, count, endpoint=False)
    steps = round(90.0 / args.angle_step_deg)
    angles = np.linspace(-90.0, 90.0, 2 * steps + 1)
    rows: list[dict] = []

    for angle in angles:
        print(f"[INITIAL ROTATION9] {angle:+.1f} deg", flush=True)
        rotated_wheels = [
            contact.rotate_about_pipe_axis(wheel, pivot, float(angle))
            for wheel in drilling
        ]
        rotated_modules = {
            name: contact.rotate_about_pipe_axis(vertices, pivot, float(angle))
            for name, vertices in drilling_modules.items()
        }
        collision_bodies = {**fixed_bodies, **rotated_modules}
        com = com_at_angle(com_model, pivot, float(angle))
        for phase in phases:
            x = 1.0 + float(phase) * 0.001
            initial = (x, contact.NORMAL_PASSIVE_SEAT_Y_M, -0.00266)
            base_pose, _, _ = contact.settle_under_gravity(
                passive, [], collision_bodies, initial, 0.0, com,
                estimation, fast=True, fixed_body_roll_deg=0.0,
            )
            pose, roll, settled = contact.settle_under_gravity(
                passive, rotated_wheels, collision_bodies, base_pose,
                0.0, com, estimation, fast=True, fixed_body_roll_deg=0.0,
            )
            details = [
                contact.clearance_details(wheel, pose, estimation, roll)
                for wheel in passive + rotated_wheels
            ]
            heights = [
                contact.local_ridge_height_mm(point, estimation)
                for _, point in details
            ]
            forces = settled["wheel_normal_force_n"]
            supports = [
                WHEEL_NAMES[i] for i, force in enumerate(forces) if force >= 2.0
            ]
            ridge = [
                WHEEL_NAMES[i] for i, force in enumerate(forces)
                if force >= 2.0 and heights[i] >= contact.RIDGE_MIN_HEIGHT_MM
            ]
            body_gap = min(settled["body_clearance_mm"].values())
            drive_state = contact.drive_first_contact(drive, pose, estimation, roll)
            rows.append({
                "module_orientation_group": "straight" if angle == 0 else "pre_rotated",
                "initial_rotation9_deg": float(angle),
                "axial_phase_mm": float(phase),
                "body_pose_m": {"x": pose[0], "y": pose[1], "z": pose[2]},
                "body_roll_deg": roll,
                "sequence": "platform passive wheels first; add drilling passive; then press drive pair",
                "support_ids": supports,
                "ridge_support_ids": ridge,
                "support_combination": "+".join(supports) or "none",
                "ridge_support_combination": "+".join(ridge) or "none",
                "wheel_normal_force_n": forces,
                "wheel_clearance_mm": [gap for gap, _ in details],
                "wheel_local_ridge_height_mm": heights,
                "minimum_body_clearance_mm": body_gap,
                "vertical_force_balance_error_n": abs(
                    settled["vertical_support_force_n"] - settled["weight_n"]
                ),
                "lateral_force_residual_n": settled["lateral_force_residual_n"],
                "required_body_roll_holding_torque_n_m": settled[
                    "roll_constraint_torque_n_m"
                ],
                "valid_without_body_penetration": bool(
                    settled["success"] and body_gap >= -contact.CONTACT_TOLERANCE_MM
                    and abs(settled["vertical_support_force_n"] - settled["weight_n"]) <= 5.0
                    and abs(settled["lateral_force_residual_n"]) <= 5.0
                ),
                **drive_state,
            })

    result = {
        "model": "body held at zero roll for initial seating; rotation9 preset before entry",
        "important_limitation": "roll holding torque, TPU deformation, cylinder load sharing and post-press drilling rotation are not solved",
        "total_states": len(rows),
        "lead_mm": lead_mm,
        "axial_step_mm": lead_mm / count,
        "initial_rotation9_angles_deg": angles.tolist(),
        "groups": summarize(rows),
        "states": rows,
    }
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"total={len(rows)} valid={sum(r['valid_without_body_penetration'] for r in rows)} groups={len(result['groups'])}")
    for group in result["groups"]:
        print(group["orientation_group"], group["count"], group["support_combination"], group["ridge_support_combination"])
    print(f"output={path}")


if __name__ == "__main__":
    main()
