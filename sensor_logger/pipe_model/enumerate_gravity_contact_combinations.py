#!/usr/bin/env python3
"""Enumerate gravity-supported passive-wheel/ridge contact combinations."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

import analyze_ridge_contact_cases as contact


WHEEL_NAMES = ["platform_1", "platform_2", "platform_3", "platform_4", "drilling_1"]
ROTATING_COMPONENTS = {"drilling_module1", "drilling_module2", "bit"}


def cached_com_model(case: dict) -> list[tuple[str, float, np.ndarray]]:
    parts = case["gravity_settled"]["component_com_estimates"]
    return [
        (name, float(data["volume_weight_fraction"]), np.asarray(data["center_sim_m"]))
        for name, data in parts.items()
    ]


def com_at_angle(
    model: list[tuple[str, float, np.ndarray]], pivot: np.ndarray, angle_deg: float
) -> np.ndarray:
    total = np.zeros(3)
    for name, fraction, center in model:
        transformed = center
        if name in ROTATING_COMPONENTS:
            transformed = contact.rotate_about_pipe_axis(
                center[None, :], pivot, angle_deg
            )[0]
        total += fraction * transformed
    return total


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--axial-step-mm", type=float, default=5.0)
    parser.add_argument("--angle-step-deg", type=float, default=15.0)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    output_dir = (args.output_dir or here / "output").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    estimation = json.loads(
        (here / "output" / "helical_ridge_estimation.json").read_text(encoding="utf-8")
    )
    previous = json.loads(
        (here / "output" / "ridge_contact_cases.json").read_text(encoding="utf-8")
    )
    assets = here.parent.parent / "raspi_tools" / "camera_stream" / "assets"
    passive, _, drilling, drilling_modules = contact.load_geometry(assets)

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
    initial_z_default = float(
        previous["cases"][0]["initial_alignment_pose_m"]["z"]
    )

    lead_mm = float(estimation["lead_mm_per_revolution"])
    axial_count = max(2, int(np.ceil(lead_mm / args.axial_step_mm)))
    phase_mm = np.linspace(0.0, lead_mm, axial_count, endpoint=False)
    angles = np.arange(-180.0, 180.0, args.angle_step_deg)
    base_x_m = 1.0
    rows: list[dict] = []

    for angle_index, angle in enumerate(angles, start=1):
        print(f"[ANGLE {angle_index:02d}/{len(angles):02d}] rotation9={angle:+.1f} deg", flush=True)
        rotated_drilling = [
            contact.rotate_about_pipe_axis(wheel, pivot, float(angle))
            for wheel in drilling
        ]
        rotated_modules = {
            name: contact.rotate_about_pipe_axis(vertices, pivot, float(angle))
            for name, vertices in drilling_modules.items()
        }
        com = com_at_angle(com_model, pivot, float(angle))
        initial_y = contact.NORMAL_PASSIVE_SEAT_Y_M
        initial_z = initial_z_default
        initial_roll = 0.0
        for phase in phase_mm:
            body_x = base_x_m + phase * 0.001
            pose, roll, settled = contact.settle_under_gravity(
                passive,
                rotated_drilling,
                rotated_modules,
                (body_x, initial_y, initial_z),
                initial_roll,
                com,
                estimation,
                fast=True,
            )
            initial_y, initial_z, initial_roll = pose[1], pose[2], roll
            wheel_sets = passive + rotated_drilling
            details = [
                contact.clearance_details(wheel, pose, estimation, roll)
                for wheel in wheel_sets
            ]
            ridge_heights = [
                contact.local_ridge_height_mm(point, estimation)
                for _, point in details
            ]
            support_ids = [
                WHEEL_NAMES[index]
                for index, force in enumerate(settled["wheel_normal_force_n"])
                if force >= 2.0
            ]
            ridge_support_ids = [
                WHEEL_NAMES[index]
                for index, force in enumerate(settled["wheel_normal_force_n"])
                if force >= 2.0 and ridge_heights[index] >= contact.RIDGE_MIN_HEIGHT_MM
            ]
            rows.append({
                "rotation9_deg": float(angle),
                "axial_phase_mm": float(phase),
                "body_x_m": pose[0],
                "body_y_m": pose[1],
                "body_z_m": pose[2],
                "body_roll_deg": roll,
                "support_combination": "+".join(support_ids) or "none",
                "ridge_support_combination": "+".join(ridge_support_ids) or "none",
                "support_ids": support_ids,
                "ridge_support_ids": ridge_support_ids,
                "wheel_clearance_mm": [detail[0] for detail in details],
                "wheel_normal_force_n": settled["wheel_normal_force_n"],
                "wheel_ridge_height_mm": ridge_heights,
                "body_clearance_mm": settled["body_clearance_mm"],
                "vertical_support_force_n": settled["vertical_support_force_n"],
                "weight_n": settled["weight_n"],
                "solver_success": settled["success"],
            })

    combinations: dict[str, dict] = {}
    for row in rows:
        key = row["ridge_support_combination"]
        entry = combinations.setdefault(key, {
            "ridge_support_combination": key,
            "count": 0,
            "sample_rotation9_deg": row["rotation9_deg"],
            "sample_axial_phase_mm": row["axial_phase_mm"],
            "support_combinations": set(),
            "minimum_body_clearance_mm": float("inf"),
            "best_body_clearance_mm": -float("inf"),
        })
        entry["count"] += 1
        entry["support_combinations"].add(row["support_combination"])
        body_gap = min(row["body_clearance_mm"].values())
        entry["minimum_body_clearance_mm"] = min(
            entry["minimum_body_clearance_mm"], body_gap
        )
        if body_gap > entry["best_body_clearance_mm"]:
            entry["best_body_clearance_mm"] = body_gap
            entry["sample_rotation9_deg"] = row["rotation9_deg"]
            entry["sample_axial_phase_mm"] = row["axial_phase_mm"]
    summary = []
    for entry in combinations.values():
        entry["support_combinations"] = sorted(entry["support_combinations"])
        entry["fraction"] = entry["count"] / len(rows)
        summary.append(entry)
    summary.sort(key=lambda item: (-item["count"], item["ridge_support_combination"]))

    result = {
        "method": "gravity settlement sweep over one inferred helical lead and rotation9",
        "wheel_ids": {str(index + 1): name for index, name in enumerate(WHEEL_NAMES)},
        "ridge_min_height_mm": contact.RIDGE_MIN_HEIGHT_MM,
        "axial_step_requested_mm": args.axial_step_mm,
        "axial_samples": len(phase_mm),
        "actual_axial_step_mm": lead_mm / len(phase_mm),
        "rotation9_step_deg": args.angle_step_deg,
        "rotation9_samples": len(angles),
        "total_states": len(rows),
        "com_method": "user-approved provisional STL enclosed-volume ratio",
        "com_model_at_rotation9_zero": previous["cases"][0]["gravity_settled"]["com_local_sim_m"],
        "combinations": summary,
        "states": rows,
    }
    json_path = output_dir / "gravity_passive_contact_combinations.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    csv_path = output_dir / "gravity_passive_contact_combinations.csv"
    flat_rows = []
    for row in rows:
        flat_rows.append({
            "rotation9_deg": row["rotation9_deg"],
            "axial_phase_mm": row["axial_phase_mm"],
            "body_roll_deg": row["body_roll_deg"],
            "support_combination": row["support_combination"],
            "ridge_support_combination": row["ridge_support_combination"],
            "vertical_support_force_n": row["vertical_support_force_n"],
            "minimum_body_clearance_mm": min(row["body_clearance_mm"].values()),
        })
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flat_rows[0]))
        writer.writeheader()
        writer.writerows(flat_rows)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"json={json_path}")
    print(f"csv={csv_path}")


if __name__ == "__main__":
    main()
