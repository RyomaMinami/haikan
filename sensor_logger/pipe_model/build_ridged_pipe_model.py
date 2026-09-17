#!/usr/bin/env python3
"""Build a full-360 pipe model from the estimated helical ridge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from build_pipe_model import build_mesh, build_wall_mesh


def ridge_depth(x_mm: np.ndarray, theta_deg: np.ndarray, start_mm: float,
                height_mm: float, width_90_mm: float, width_80_mm: float,
                half_height_width_mm: float, lead_mm: float,
                handedness: str) -> np.ndarray:
    """Return a smooth representative ridge profile from measured widths."""
    direction = 1.0 if handedness == "right" else -1.0
    phase = np.mod(
        x_mm[:, None] - start_mm
        - direction * lead_mm * theta_deg[None, :] / 360.0
        + 0.5 * lead_mm,
        lead_mm,
    ) - 0.5 * lead_mm
    distance = np.abs(phase)
    distance_knots = np.array([
        0.0,
        0.5 * width_90_mm,
        0.5 * width_80_mm,
        0.5 * half_height_width_mm,
        0.5 * lead_mm,
    ])
    height_knots = height_mm * np.array([1.0, 0.9, 0.8, 0.5, 0.0])
    return np.interp(
        distance.ravel(), distance_knots, height_knots,
        left=height_mm, right=0.0,
    ).reshape(distance.shape)


def write_preview(path: Path, x: np.ndarray, theta: np.ndarray,
                  base_radius: np.ndarray, ridged_radius: np.ndarray,
                  ridge: np.ndarray, lead_mm: float, height_mm: float,
                  width_90_mm: float) -> None:
    fig = plt.figure(figsize=(15, 8))
    ax3d = fig.add_subplot(1, 2, 1, projection="3d")
    stride = max(1, len(x) // 180)
    xx, tt = np.meshgrid(x[::stride], np.radians(theta), indexing="ij")
    rr = ridged_radius[::stride]
    yy = rr * np.cos(tt)
    zz = rr * np.sin(tt)
    face = plt.colormaps["Reds"](
        np.clip(ridge[::stride] / max(height_mm, 1e-9), 0.0, 1.0)
    )
    ax3d.plot_surface(xx, yy, zz, facecolors=face, linewidth=0,
                      antialiased=False, shade=False)
    ax3d.set_xlabel("Pipe axis X [mm]")
    ax3d.set_ylabel("Y [mm]")
    ax3d.set_zlabel("Z [mm]")
    ax3d.set_title("Estimated full-360 one-start inward helical ridge")
    ax3d.set_box_aspect((max(x) - min(x), 240, 240))
    ax3d.view_init(elev=24, azim=-58)

    ax = fig.add_subplot(1, 2, 2)
    top_index = int(np.argmin(np.abs(theta)))
    ax.plot(x, base_radius[:, top_index], color="#777777", lw=1.2,
            label="nominal full-360 base surface")
    ax.plot(x, ridged_radius[:, top_index], color="#c62828", lw=1.5,
            label="surface with ridges")
    top_ridge = ridge[:, top_index]
    ax.fill_between(
        x, ridged_radius[:, top_index], base_radius[:, top_index],
        where=top_ridge > 0, color="#ef5350", alpha=0.45,
        label=f"{height_mm:.2f} mm inward representative ridge",
    )
    ax.set_xlabel("Pipe axis X [mm]")
    ax.set_ylabel("Inner radius at command 0 deg [mm]")
    ax.set_title(
        f"Estimated helix: height {height_mm:.2f} mm / "
        f"90% width {width_90_mm:.1f} mm / lead {lead_mm:.1f} mm"
    )
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=here / "output")
    parser.add_argument("--axial-step-mm", type=float, default=2.0)
    parser.add_argument("--angular-step-deg", type=float, default=2.0)
    parser.add_argument("--nominal-inner-radius-mm", type=float, default=120.0)
    parser.add_argument("--wall-thickness-mm", type=float, default=5.0)
    parser.add_argument(
        "--estimation-json", type=Path,
        default=here / "output" / "helical_ridge_estimation.json",
    )
    parser.add_argument("--ridge-height-mm", type=float)
    parser.add_argument("--ridge-width-90-mm", type=float)
    parser.add_argument("--ridge-width-80-mm", type=float)
    parser.add_argument("--ridge-half-height-width-mm", type=float)
    parser.add_argument("--ridge-lead-mm", type=float)
    parser.add_argument("--ridge-start-mm", type=float)
    parser.add_argument("--handedness", choices=("right", "left"))
    args = parser.parse_args()

    estimation_path = args.estimation_json.resolve()
    estimation = json.loads(estimation_path.read_text(encoding="utf-8"))
    profile = estimation["profile"]
    ridge_height_mm = (
        args.ridge_height_mm
        if args.ridge_height_mm is not None
        else float(profile["peak_to_valley_mm"])
    )
    width_90_mm = (
        args.ridge_width_90_mm
        if args.ridge_width_90_mm is not None
        else float(profile["crest_width_90pct_height_mm"])
    )
    width_80_mm = (
        args.ridge_width_80_mm
        if args.ridge_width_80_mm is not None
        else float(profile["crest_width_80pct_height_mm"])
    )
    half_height_width_mm = (
        args.ridge_half_height_width_mm
        if args.ridge_half_height_width_mm is not None
        else float(profile["half_height_width_mm"])
    )
    lead_mm = (
        args.ridge_lead_mm
        if args.ridge_lead_mm is not None
        else float(estimation["lead_mm_per_revolution"])
    )
    start_mm = (
        args.ridge_start_mm
        if args.ridge_start_mm is not None
        else float(profile["crest_phase_at_theta0_mm"])
    )
    handedness = args.handedness or (
        "right" if str(estimation["handedness"]).startswith("right") else "left"
    )

    widths = (width_90_mm, width_80_mm, half_height_width_mm)
    if not (0 < widths[0] < widths[1] < widths[2] < lead_mm):
        raise SystemExit(
            "ridge widths must satisfy 0 < width90 < width80 < half-width < lead"
        )
    if min(
        ridge_height_mm, args.wall_thickness_mm, args.axial_step_mm,
        args.angular_step_deg, args.nominal_inner_radius_mm,
    ) <= 0:
        raise SystemExit("dimensions and grid steps must be positive")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    measured_range = estimation["measured_range"]
    x_min, x_max = (float(v) for v in measured_range["x_mm"])
    x = np.arange(x_min, x_max + 0.5 * args.axial_step_mm, args.axial_step_mm)
    theta = np.arange(-180.0, 180.0, args.angular_step_deg)
    base_radius = np.full(
        (len(x), len(theta)), args.nominal_inner_radius_mm, dtype=float,
    )

    # Subtracting the inferred profile makes the ridge protrude into the cavity.
    ridge = ridge_depth(
        x, theta, start_mm, ridge_height_mm, width_90_mm, width_80_mm,
        half_height_width_mm, lead_mm, handedness,
    )
    ridged_radius = base_radius - ridge

    inner_mesh = build_mesh(x, theta, ridged_radius, wrap_angle=True)
    wall_mesh = build_wall_mesh(
        x, theta, ridged_radius, args.wall_thickness_mm, wrap_angle=True
    )
    # Keep stable output names so the simulator always follows the latest
    # values in helical_ridge_estimation.json.
    stem = "helix_estimated"
    inner_path = output_dir / f"pipe_inner_full360_{stem}.stl"
    wall_path = output_dir / f"pipe_wall_5mm_full360_{stem}.stl"
    preview_path = output_dir / f"pipe_full360_{stem}_preview.png"
    metadata_path = output_dir / f"pipe_full360_{stem}.json"
    inner_mesh.export(inner_path)
    wall_mesh.export(wall_path)
    write_preview(
        preview_path, x, theta, base_radius, ridged_radius, ridge,
        lead_mm, ridge_height_mm, width_90_mm,
    )

    first_turn = start_mm + np.ceil((float(x[0]) - start_mm) / lead_mm) * lead_mm
    ridge_starts = np.arange(first_turn, float(x[-1]) + lead_mm, lead_mm)
    complete_turns = int(np.floor((x[-1] - x[0]) / lead_mm))
    metadata = {
        "source_estimation_json": estimation_path.name,
        "base_surface": "nominal 240 mm inner-diameter cylinder inferred to full 360 degrees",
        "nominal_inner_radius_mm": args.nominal_inner_radius_mm,
        "ridge_type": "one-start helix",
        "ridge_profile": "piecewise-linear interpolation of measured width levels",
        "ridge_handedness": handedness,
        "ridge_direction": "inward into pipe cavity",
        "ridge_peak_to_valley_height_mm": ridge_height_mm,
        "ridge_crest_width_90pct_mm": width_90_mm,
        "ridge_crest_width_80pct_mm": width_80_mm,
        "ridge_half_height_width_mm": half_height_width_mm,
        "ridge_lead_mm_per_revolution": lead_mm,
        "ridge_start_at_theta0_mm": start_mm,
        "ridge_start_positions_mm": [float(v) for v in ridge_starts if v <= x[-1]],
        "complete_turn_count": complete_turns,
        "axial_range_mm": [float(x[0]), float(x[-1])],
        "angular_coverage_deg": 360.0,
        "angular_grid_deg": [float(theta[0]), float(theta[-1]), args.angular_step_deg],
        "wall_thickness_mm": args.wall_thickness_mm,
        "wall_mesh_watertight": bool(wall_mesh.is_watertight),
        "outputs": {
            "inner_surface_stl": inner_path.name,
            "wall_stl": wall_path.name,
            "preview_png": preview_path.name,
        },
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
