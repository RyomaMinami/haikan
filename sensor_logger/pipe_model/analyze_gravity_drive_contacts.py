#!/usr/bin/env python3
"""Classify first drive-tire contacts after gravity seating, before load sharing."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import analyze_ridge_contact_cases as contact


def evaluate(state: dict, drive: list[np.ndarray], estimation: dict) -> dict:
    pose = tuple(state["body_pose_m"][axis] for axis in ("x", "y", "z"))
    roll = float(state["body_roll_deg"])
    drive_state = contact.drive_first_contact(drive, pose, estimation, roll)
    strokes = drive_state["drive_required_stroke_mm"]
    reachable = [stroke for stroke in strokes if stroke is not None]
    first_stroke = drive_state["cylinder_first_contact_stroke_mm"]
    slider = (
        contact.SLIDER6_MIN_MM + first_stroke
        if first_stroke is not None else contact.SLIDER6_MAX_MM
    )
    details = [
        contact.clearance_details(wheel, pose, estimation, roll, slider_mm=slider)
        if index in contact.ACTIVE_DRIVE_WHEEL_INDICES else None
        for index, wheel in enumerate(drive)
    ]
    gaps = [float(detail[0]) if detail is not None else None for detail in details]
    heights = [
        contact.local_ridge_height_mm(detail[1], estimation)
        if detail is not None else None
        for detail in details
    ]
    first_ids = [
        i + 1 for i, gap in enumerate(gaps)
        if gap is not None and gap <= contact.CONTACT_TOLERANCE_MM
    ]
    ridge_ids = [
        i for i in first_ids
        if heights[i - 1] >= contact.RIDGE_MIN_HEIGHT_MM
    ]
    return {
        "rotation9_deg": float(state["rotation9_deg"]),
        "axial_phase_mm": float(state["axial_phase_mm"]),
        "passive_ridge_supports": state["passive_ridge_supports"],
        "body_roll_deg": roll,
        "first_contact_stroke_mm": first_stroke,
        "first_contact_drive_wheels": first_ids,
        "first_contact_drive_wheels_on_ridge": ridge_ids,
        "drive_contact_enabled": drive_state["drive_contact_enabled"],
        "other_wheel_clearance_at_first_contact_mm": gaps,
        "drive_required_stroke_mm": strokes,
        "local_ridge_height_at_wheel_mm": heights,
        "next_wheel_stroke_difference_mm": (
            sorted(reachable)[1] - sorted(reachable)[0]
            if len(reachable) > 1 else None
        ),
    }


def main() -> None:
    here = Path(__file__).resolve().parent
    output = here / "output"
    estimation = json.loads(
        (output / "helical_ridge_estimation.json").read_text(encoding="utf-8")
    )
    sweep = json.loads(
        (output / "gravity_passive_contact_combinations.json").read_text(
            encoding="utf-8"
        )
    )
    validated = json.loads(
        (output / "gravity_passive_contact_representatives.json").read_text(
            encoding="utf-8"
        )
    )
    assets = here.parent.parent / "raspi_tools" / "camera_stream" / "assets"
    _, drive, _, _ = contact.load_geometry(assets)

    # The coarse sweep is only a search. Reject obvious penetration or force
    # imbalance here, and report fully validated cases separately below.
    coarse_states = []
    for state in sweep["states"]:
        if not state["solver_success"]:
            continue
        if min(state["body_clearance_mm"].values()) < -contact.CONTACT_TOLERANCE_MM:
            continue
        if abs(state["vertical_support_force_n"] - state["weight_n"]) > 5.0:
            continue
        coarse_states.append({
            "body_pose_m": {
                "x": state["body_x_m"],
                "y": state["body_y_m"],
                "z": state["body_z_m"],
            },
            "body_roll_deg": state["body_roll_deg"],
            "rotation9_deg": state["rotation9_deg"],
            "axial_phase_mm": state["axial_phase_mm"],
            "passive_ridge_supports": state["ridge_support_ids"],
        })
    coarse_results = [evaluate(state, drive, estimation) for state in coarse_states]

    high_accuracy_states = [{
        "body_pose_m": case["body_pose_m"],
        "body_roll_deg": case["body_roll_deg"],
        "rotation9_deg": case["rotation9_deg"],
        "axial_phase_mm": case["axial_phase_mm"],
        "passive_ridge_supports": case["case_label"].split("ridge supports ")[-1],
    } for case in validated["simulator_cases"]]
    high_accuracy_results = [
        evaluate(state, drive, estimation) for state in high_accuracy_states
    ]
    result = {
        "method": "gravity seat, then extend cylinder to first drive-tire contact",
        "limits": (
            "First geometric touch only; no cylinder pressure, tire compliance, "
            "load redistribution, or motion after first touch is solved."
        ),
        "drive_wheel_order_cad_centers_mm": contact.DRIVE_WHEELS_MODULE_CAD_MM.tolist(),
        "contact_tolerance_mm": contact.CONTACT_TOLERANCE_MM,
        "ridge_height_threshold_mm": contact.RIDGE_MIN_HEIGHT_MM,
        "coarse_valid_states": coarse_results,
        "high_accuracy_representatives": high_accuracy_results,
    }
    path = output / "gravity_drive_first_contacts.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"coarse_valid_states={len(coarse_results)}")
    for index, item in enumerate(high_accuracy_results, 7):
        print(
            f"case {index}: drive {item['first_contact_drive_wheels']}, "
            f"ridge {item['first_contact_drive_wheels_on_ridge']}, "
            f"stroke {item['first_contact_stroke_mm']:.3f} mm, "
            f"next wheel +{item['next_wheel_stroke_difference_mm']:.3f} mm"
        )
    print(f"output={path}")


if __name__ == "__main__":
    main()
