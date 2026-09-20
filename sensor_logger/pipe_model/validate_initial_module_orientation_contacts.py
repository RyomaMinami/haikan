#!/usr/bin/env python3
"""Recheck straight/pre-rotated passive-contact representatives at full mesh resolution."""

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
    coarse = json.loads(
        (output / "initial_module_orientation_contacts.json").read_text(
            encoding="utf-8"
        )
    )
    estimation = json.loads(
        (output / "helical_ridge_estimation.json").read_text(encoding="utf-8")
    )
    previous = json.loads(
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
    com_model = cached_com_model(previous["cases"][0])
    validated = []
    simulator_cases = []
    for index, group in enumerate(coarse["groups"], 1):
        representative = group["representative"]
        angle = float(representative["initial_rotation9_deg"])
        print(f"[VALIDATE {index}/{len(coarse['groups'])}] {group['orientation_group']} {angle:+.1f} deg", flush=True)
        rotated_wheels = [
            contact.rotate_about_pipe_axis(wheel, pivot, angle)
            for wheel in drilling
        ]
        rotated_modules = {
            name: contact.rotate_about_pipe_axis(vertices, pivot, angle)
            for name, vertices in drilling_modules.items()
        }
        com = com_at_angle(com_model, pivot, angle)
        prior_pose = representative["body_pose_m"]
        initial = tuple(prior_pose[axis] for axis in ("x", "y", "z"))
        pose, roll, settled = contact.settle_under_gravity(
            passive, rotated_wheels, rotated_modules, initial, 0.0,
            com, estimation, fast=False, fixed_body_roll_deg=0.0,
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
        supports = [WHEEL_NAMES[i] for i, force in enumerate(forces) if force >= 2.0]
        ridge = [
            WHEEL_NAMES[i] for i, force in enumerate(forces)
            if force >= 2.0 and heights[i] >= contact.RIDGE_MIN_HEIGHT_MM
        ]
        body_gap = min(settled["body_clearance_mm"].values())
        force_error = abs(settled["vertical_support_force_n"] - settled["weight_n"])
        valid = bool(settled["success"] and body_gap >= -0.005 and force_error <= 5.0)
        drive_state = contact.drive_first_contact(drive, pose, estimation, roll)
        row = {
            "coarse_group": group["orientation_group"],
            "initial_rotation9_deg": angle,
            "axial_phase_mm": representative["axial_phase_mm"],
            "body_pose_m": {"x": pose[0], "y": pose[1], "z": pose[2]},
            "body_roll_deg": roll,
            "support_ids": supports,
            "ridge_support_ids": ridge,
            "wheel_clearance_mm": [gap for gap, _ in details],
            "wheel_normal_force_n": forces,
            "wheel_local_ridge_height_mm": heights,
            "body_clearance_mm": settled["body_clearance_mm"],
            "minimum_body_clearance_mm": body_gap,
            "vertical_force_balance_error_n": force_error,
            "valid_without_body_penetration": valid,
            **drive_state,
        }
        validated.append(row)
        if not valid:
            continue
        module_details = {
            name: contact.clearance_details(vertices, pose, estimation, roll)
            for name, vertices in rotated_modules.items()
        }
        closest_name, (module_gap, module_point) = min(
            module_details.items(), key=lambda item: item[1][0]
        )
        support_flags = [force >= 2.0 for force in forces]
        ridge_text = "+".join(ridge) or "none"
        simulator_cases.append({
            "case_label": f"insertion {group['orientation_group']} {angle:+.0f} deg / ridge {ridge_text}",
            "source": "initial_module_orientation_contacts",
            "rotation9_deg": angle,
            "axial_phase_mm": representative["axial_phase_mm"],
            "valid_configuration": True,
            "diagnostic_reason": "full-mesh validation at body roll zero",
            "passive_wheels_on_ridge": [
                i + 1 for i in range(4) if WHEEL_NAMES[i] in ridge
            ],
            "body_pose_m": row["body_pose_m"],
            "body_roll_deg": 0.0,
            "passive_clearance_mm": row["wheel_clearance_mm"][:4],
            "passive_contact": support_flags[:4],
            "drilling_wheel_clearance_mm": [row["wheel_clearance_mm"][4]],
            "drilling_contact": [support_flags[4]],
            "gravity_settled": {
                "success": settled["success"],
                "sequence": "platform passive first, drilling passive next, drive pair last",
                "mass_kg": contact.ROBOT_MASS_KG,
                "gravity_m_s2": contact.GRAVITY_M_S2,
                "com_method": "user-approved provisional STL enclosed-volume ratio",
                "contact_wheels": [i + 1 for i, flag in enumerate(support_flags) if flag],
                "wheel_clearance_mm": row["wheel_clearance_mm"],
                "wheel_normal_force_n": forces,
                "body_clearance_mm": settled["body_clearance_mm"],
                "vertical_support_force_n": settled["vertical_support_force_n"],
                "weight_n": settled["weight_n"],
            },
            "drilling_module_after_press": {
                "sequence": "platform passive, drilling passive, drive first contact",
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
            **drive_state,
        })

    simulator_cases.sort(
        key=lambda case: (
            case["rotation9_deg"] != 0.0,
            case["rotation9_deg"],
            case["axial_phase_mm"],
        )
    )
    result = {
        "method": "full-mesh re-settlement of straight/pre-rotated insertion representatives",
        "body_roll_constraint_deg": 0.0,
        "maximum_allowed_body_penetration_mm": 0.005,
        "validated_representatives": validated,
        "simulator_cases": simulator_cases,
    }
    path = output / "initial_module_orientation_validated.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"valid={len(simulator_cases)}/{len(validated)}")
    for row in validated:
        print(
            f"{row['initial_rotation9_deg']:+.0f} deg "
            f"supports={'+'.join(row['support_ids']) or 'none'} "
            f"ridge={'+'.join(row['ridge_support_ids']) or 'none'} "
            f"gap={row['minimum_body_clearance_mm']:.3f} mm "
            f"valid={row['valid_without_body_penetration']}"
        )
    print(f"output={path}")


if __name__ == "__main__":
    main()
