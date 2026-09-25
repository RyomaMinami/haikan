#!/usr/bin/env python3
"""Estimate axial force needed for the pressurized robot to cross one ridge lead."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

import analyze_ridge_contact_cases as contact
from build_pipe_model import common_grid, load_tracks
from estimate_helical_ridge import folded_profile


DEFAULT_FRICTION_COEFFICIENT = 0.10
AXIAL_SAMPLE_MM = 0.25
DRIVE_EFFECTIVE_RADIUS_MM = 27.0


def profile_knots(estimation: dict) -> tuple[np.ndarray, np.ndarray]:
    profile = estimation["profile"]
    lead = float(estimation["lead_mm_per_revolution"])
    height = float(profile["peak_to_valley_mm"])
    distance = np.array([
        0.0,
        0.5 * float(profile["crest_width_90pct_height_mm"]),
        0.5 * float(profile["crest_width_80pct_height_mm"]),
        0.5 * float(profile["half_height_width_mm"]),
        0.5 * lead,
    ])
    heights = height * np.array([1.0, 0.9, 0.8, 0.5, 0.0])
    return distance, heights


def ridge_height_and_slope(
    phase_mm: np.ndarray, estimation: dict
) -> tuple[np.ndarray, np.ndarray]:
    """Return inward ridge height and dh/dx for wrapped phase."""
    lead = float(estimation["lead_mm_per_revolution"])
    wrapped = np.mod(phase_mm + 0.5 * lead, lead) - 0.5 * lead
    distance = np.abs(wrapped)
    knots, heights = profile_knots(estimation)
    height = np.interp(distance, knots, heights)
    segment = np.clip(np.searchsorted(knots, distance, side="right") - 1, 0, len(knots) - 2)
    dh_dd = (heights[segment + 1] - heights[segment]) / (
        knots[segment + 1] - knots[segment]
    )
    slope = dh_dd * np.sign(wrapped)
    slope[np.isclose(wrapped, 0.0)] = 0.0
    return height, slope


def measured_folded_profile(
    source_csv: Path, estimation: dict
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rebuild the 1 mm phase-folded profile used by ridge estimation."""
    tracks = load_tracks(source_csv)
    x, theta, radius = common_grid(tracks, axial_step_mm=2.0, smoothing_window=7)
    residual = radius - np.mean(radius, axis=0, keepdims=True)
    residual = residual - np.mean(residual, axis=1, keepdims=True) + np.mean(residual)
    x_grid = np.broadcast_to(x[:, None], radius.shape)
    theta_grid = np.broadcast_to(theta[None, :], radius.shape)
    exclusion = estimation["branch_exclusion"]
    branch_arc_mm = 120.0 * np.deg2rad(
        theta_grid - float(exclusion["center_angle_deg"])
    )
    branch_mask = (
        (x_grid - float(exclusion["center_x_mm"])) ** 2 + branch_arc_mm**2
        <= float(exclusion["radius_mm"]) ** 2
    )
    valid = (
        (~branch_mask)
        & (x_grid > x[0] + 12.0)
        & (x_grid < x[-1] - 15.0)
    )
    phase, radial_profile = folded_profile(
        residual,
        x_grid,
        theta_grid,
        valid,
        float(estimation["lead_mm_per_revolution"]),
        int(estimation["sign"]),
    )
    spacing = float(np.median(np.diff(phase)))
    slope = (np.roll(radial_profile, -1) - np.roll(radial_profile, 1)) / (
        2.0 * spacing
    )
    height = radial_profile - float(np.min(radial_profile))
    return phase, height, slope


def periodic_interp(
    phase_mm: np.ndarray, grid: np.ndarray, values: np.ndarray, lead: float
) -> np.ndarray:
    wrapped = np.mod(phase_mm, lead)
    extended_grid = np.r_[grid[-1] - lead, grid, grid[0] + lead]
    extended_values = np.r_[values[-1], values, values[0]]
    return np.interp(wrapped, extended_grid, extended_values)


def contact_phase_mm(point: np.ndarray, estimation: dict) -> float:
    x_mm = float(point[0] * 1000.0)
    theta_deg = float(np.degrees(np.arctan2(point[2], point[1])))
    return (
        x_mm
        - float(estimation["profile"]["crest_phase_at_theta0_mm"])
        - float(estimation["lead_mm_per_revolution"]) * theta_deg / 360.0
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pressure-json",
        default="pressurized_contact_distribution_0p7mpa.json",
    )
    parser.add_argument(
        "--profile-shape",
        choices=("measured", "symmetric-triangle"),
        default="measured",
    )
    parser.add_argument("--ridge-width-mm", type=float, default=30.0)
    parser.add_argument(
        "--friction-coefficient",
        type=float,
        default=DEFAULT_FRICTION_COEFFICIENT,
    )
    parser.add_argument("--output-stem", default="ridge_crossing_force_0p7mpa")
    args = parser.parse_args()
    here = Path(__file__).resolve().parent
    output = here / "output"
    estimation = json.loads(
        (output / "helical_ridge_estimation.json").read_text(encoding="utf-8")
    )
    pressure = json.loads(
        (output / args.pressure_json).read_text(
            encoding="utf-8"
        )
    )
    insertion = json.loads(
        (output / "initial_module_orientation_validated.json").read_text(
            encoding="utf-8"
        )
    )
    assets = here.parent.parent / "raspi_tools" / "camera_stream" / "assets"
    passive, drive, drilling, drilling_modules = contact.load_geometry(assets)
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

    lead = float(estimation["lead_mm_per_revolution"])
    shifts = np.arange(0.0, lead, AXIAL_SAMPLE_MM)
    source_csv = here.parent / str(estimation["source_csv"])
    if args.profile_shape == "measured":
        phase_grid, measured_height, measured_slope = measured_folded_profile(
            source_csv, estimation
        )
        method = (
            "fixed post-press normal loads projected through the 1 mm "
            "phase-folded measured helical profile over one lead"
        )
    else:
        phase_grid = np.arange(0.0, lead, 0.25)
        distance = np.minimum(phase_grid, lead - phase_grid)
        half_width = 0.5 * args.ridge_width_mm
        measured_height = np.maximum(
            0.0, float(estimation["profile"]["peak_to_valley_mm"])
            * (1.0 - distance / half_width),
        )
        measured_slope = np.zeros_like(phase_grid)
        on_ridge = (distance > 0.0) & (distance < half_width)
        measured_slope[on_ridge] = (
            -float(estimation["profile"]["peak_to_valley_mm"]) / half_width
            * np.where(phase_grid[on_ridge] < 0.5 * lead, 1.0, -1.0)
        )
        method = (
            f"fixed post-press normal loads projected through a symmetric "
            f"triangular helical ridge of total width {args.ridge_width_mm:g} mm"
        )
    maximum_surface_slope = float(np.max(np.abs(measured_slope)))
    rows = []

    for index, (case, state) in enumerate(
        zip(insertion["simulator_cases"], pressure["primary_cases"]), start=11
    ):
        angle = float(state["rotation9_deg"])
        pose = tuple(state["body_pose_m"][axis] for axis in ("x", "y", "z"))
        slider = float(state["slider6_mm"])
        rotated_drilling = contact.rotate_about_pipe_axis(drilling[0], pivot, angle)
        wheel_geometry = passive + [rotated_drilling] + active_drive
        wheel_forces = (
            list(state["passive_normal_force_n"])
            + list(state["drive_normal_force_n"])
        )
        phases = []
        for wheel_index, wheel in enumerate(wheel_geometry):
            detail = contact.clearance_details(
                wheel,
                pose,
                estimation,
                slider_mm=(slider if wheel_index >= 5 else contact.SLIDER6_MIN_MM),
            )
            phases.append(contact_phase_mm(detail[1], estimation))

        force_curve = np.zeros_like(shifts)
        absolute_bound_curve = np.zeros_like(shifts)
        for normal_force, phase in zip(wheel_forces, phases):
            if normal_force < 2.0:
                continue
            slope = periodic_interp(
                phase + shifts, phase_grid, measured_slope, lead
            )
            force_curve += normal_force * slope
            absolute_bound_curve += normal_force * np.abs(slope)

        maximum_forward = float(np.max(force_curve))
        maximum_reverse = float(-np.min(force_curve))
        simultaneous_absolute = float(np.max(absolute_bound_curve))
        total_normal = float(sum(wheel_forces))
        drive_normal = float(sum(state["drive_normal_force_n"]))
        traction_limit = args.friction_coefficient * drive_normal
        required_total_wheel_torque = maximum_forward * DRIVE_EFFECTIVE_RADIUS_MM / 1000.0
        body_contact = bool(state["body_contact_ids"])
        rows.append({
            "case": index,
            "rotation9_deg": angle,
            "wheel_contact_normal_force_n": total_normal,
            "drive_normal_force_n": drive_normal,
            "maximum_forward_ridge_force_n": maximum_forward,
            "maximum_reverse_ridge_force_n": maximum_reverse,
            "simultaneous_absolute_ridge_force_n": simultaneous_absolute,
            "conservative_all_contacts_same_slope_n": total_normal * maximum_surface_slope,
            "traction_limit_n": traction_limit,
            "traction_margin_after_ridge_n": traction_limit - maximum_forward,
            "required_total_drive_wheel_torque_n_m": required_total_wheel_torque,
            "required_torque_per_drive_wheel_if_equal_n_m": required_total_wheel_torque / 2.0,
            "body_contact_ids": state["body_contact_ids"],
            "wheel_only_ridge_stack_unlikely": bool(
                maximum_forward < traction_limit and not body_contact
            ),
        })

    ridge_height = float(estimation["profile"]["peak_to_valley_mm"])
    vertical_step_ratio = float(
        np.sqrt(2.0 * DRIVE_EFFECTIVE_RADIUS_MM * ridge_height - ridge_height**2)
        / (DRIVE_EFFECTIVE_RADIUS_MM - ridge_height)
    )
    result = {
        "method": method,
        "profile_source_csv": str(source_csv.relative_to(here.parent.parent)),
        "post_press_load_source": args.pressure_json,
        "profile_phase_step_mm": float(np.median(np.diff(phase_grid))),
        "ridge_height_mm": ridge_height,
        "ridge_lead_mm": lead,
        "ridge_profile_shape": args.profile_shape,
        "ridge_total_width_mm": (
            args.ridge_width_mm if args.profile_shape == "symmetric-triangle" else None
        ),
        "maximum_inferred_surface_slope": maximum_surface_slope,
        "maximum_inferred_surface_angle_deg": float(np.degrees(np.arctan(maximum_surface_slope))),
        "friction_coefficient": args.friction_coefficient,
        "drive_effective_radius_mm": DRIVE_EFFECTIVE_RADIUS_MM,
        "vertical_step_force_to_normal_ratio_reference": vertical_step_ratio,
        "important_limitations": [
            "Normal loads are held at each case's 0.7 MPa equilibrium values while crossing.",
            "The ridge phase is based on a helix fit with R-squared 0.424; local geometry error could change force.",
            "Coupon-derived stiffness does not determine completed-wheel deformation or rolling hysteresis; a radial compression test of one completed wheel is still required.",
            "Body contacts are flagged separately because a non-rolling edge can snag even when wheels can climb.",
            f"The traction limit uses mu={args.friction_coefficient:g} and does not prove that the motor can supply the corresponding torque.",
        ],
        "cases": rows,
        "summary": {
            "maximum_forward_ridge_force_n": max(r["maximum_forward_ridge_force_n"] for r in rows),
            "minimum_forward_ridge_force_n": min(r["maximum_forward_ridge_force_n"] for r in rows),
            "maximum_conservative_same_slope_force_n": max(r["conservative_all_contacts_same_slope_n"] for r in rows),
            "minimum_traction_margin_n": min(r["traction_margin_after_ridge_n"] for r in rows),
            "traction_pass_cases": sum(
                r["traction_margin_after_ridge_n"] > 0.0 for r in rows
            ),
            "wheel_only_clear_cases": sum(r["wheel_only_ridge_stack_unlikely"] for r in rows),
            "body_contact_risk_cases": sum(bool(r["body_contact_ids"]) for r in rows),
        },
    }
    json_path = output / f"{args.output_stem}.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    csv_path = output / f"{args.output_stem}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    figure_path = output / f"{args.output_stem}.png"
    case_ids = np.array([row["case"] for row in rows])
    ridge_force = np.array([row["maximum_forward_ridge_force_n"] for row in rows])
    traction = np.array([row["traction_limit_n"] for row in rows])
    conservative = np.array([
        row["conservative_all_contacts_same_slope_n"] for row in rows
    ])
    body_mask = np.array([bool(row["body_contact_ids"]) for row in rows])
    fig, axis = plt.subplots(figsize=(13, 6.5))
    axis.plot(
        case_ids, traction, "o-", color="#167d45",
        label=f"traction limit (mu={args.friction_coefficient:g})",
    )
    axis.plot(case_ids, ridge_force, "o-", color="#1665a8", label="phase-aware ridge force")
    axis.plot(
        case_ids, conservative, "--", color="#e28b21",
        label="all contacts on maximum slope (conservative)",
    )
    axis.scatter(
        case_ids[body_mask], ridge_force[body_mask], marker="x", s=80,
        linewidths=2.0, color="#c62828", label="body-contact risk",
    )
    axis.axhline(4.0 * 9.80665, color="#6f42c1", ls=":", lw=2,
                 label="4 kgf spring-scale assistance (39.2 N)")
    profile_label = (
        "measured folded profile"
        if args.profile_shape == "measured"
        else f"symmetric triangular ridge, width {args.ridge_width_mm:g} mm"
    )
    axis.set(
        xlabel="Simulator case",
        ylabel="Axial force [N]",
        title=(
            "Estimated helical-ridge crossing force after 0.7 MPa clamping\n"
            + profile_label
        ),
        xticks=case_ids,
    )
    axis.grid(alpha=0.25)
    axis.legend(ncol=2)
    fig.tight_layout()
    fig.savefig(figure_path, dpi=180)
    plt.close(fig)
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"surface_angle={result['maximum_inferred_surface_angle_deg']:.3f} deg")
    print(f"vertical_step_reference_ratio={vertical_step_ratio:.3f}")
    print(f"output={json_path}")
    print(f"output={csv_path}")
    print(f"output={figure_path}")


if __name__ == "__main__":
    main()
