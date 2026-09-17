#!/usr/bin/env python3
"""Estimate a one-start helical pipe ridge from axis-corrected LK-G85A data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import find_peaks, peak_widths, savgol_filter

from build_pipe_model import common_grid, load_tracks


def design_matrix(phase: np.ndarray, harmonics: int) -> np.ndarray:
    columns = [np.ones_like(phase)]
    for order in range(1, harmonics + 1):
        columns.extend((np.sin(order * phase), np.cos(order * phase)))
    return np.column_stack(columns)


def fit_candidate(values: np.ndarray, x_mm: np.ndarray, theta_rad: np.ndarray,
                  lead_mm: float, sign: int, harmonics: int) -> tuple[float, np.ndarray]:
    phase = 2.0 * np.pi * x_mm / lead_mm + sign * theta_rad
    matrix = design_matrix(phase, harmonics)
    coefficients = np.linalg.lstsq(matrix, values, rcond=None)[0]
    predicted = matrix @ coefficients
    denominator = np.sum((values - np.mean(values)) ** 2)
    r_squared = 1.0 - np.sum((values - predicted) ** 2) / denominator
    return float(r_squared), coefficients


def scan_lead(values: np.ndarray, x_mm: np.ndarray, theta_rad: np.ndarray,
              lead_min_mm: float, lead_max_mm: float, lead_step_mm: float,
              harmonics: int) -> dict[str, object]:
    best: dict[str, object] | None = None
    for lead_mm in np.arange(lead_min_mm, lead_max_mm + 0.5 * lead_step_mm,
                             lead_step_mm):
        for sign in (-1, 1):
            r_squared, coefficients = fit_candidate(
                values, x_mm, theta_rad, float(lead_mm), sign, harmonics
            )
            candidate = {
                "r_squared": r_squared,
                "lead_mm_per_revolution": float(lead_mm),
                "sign": sign,
                "coefficients": coefficients,
            }
            if best is None or r_squared > float(best["r_squared"]):
                best = candidate
    assert best is not None
    return best


def folded_profile(residual: np.ndarray, x_grid: np.ndarray,
                   theta_grid: np.ndarray, valid: np.ndarray,
                   lead_mm: float, sign: int,
                   bin_mm: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    # This axial phase is equivalent to 2*pi*x/lead + sign*theta.
    phase_mm = np.mod(
        x_grid + sign * lead_mm * theta_grid / 360.0,
        lead_mm,
    )
    edges = np.arange(0.0, lead_mm + bin_mm, bin_mm)
    centers = 0.5 * (edges[:-1] + edges[1:])
    profile = np.full(len(centers), np.nan)
    for index in range(len(centers)):
        selection = valid & (phase_mm >= edges[index]) & (phase_mm < edges[index + 1])
        samples = residual[selection]
        if len(samples):
            profile[index] = np.median(samples)
    profile = np.where(np.isfinite(profile), profile, np.nanmedian(profile))
    half = 5
    padded = np.r_[profile[-half:], profile, profile[:half]]
    profile = savgol_filter(padded, 11, 2)[half:-half]
    return centers, profile


def profile_metrics(phase_mm: np.ndarray, profile: np.ndarray) -> dict[str, float]:
    repeated = np.r_[profile, profile, profile]
    peaks, properties = find_peaks(
        repeated, prominence=0.2, distance=len(profile) * 0.7
    )
    candidates = [
        (float(properties["prominences"][index]), int(peak), index)
        for index, peak in enumerate(peaks)
        if len(profile) <= peak < 2 * len(profile)
    ]
    if not candidates:
        raise RuntimeError("No stable crest was found in the folded helical profile")
    prominence, peak, _property_index = max(candidates)
    widths = {
        "crest_width_90pct_height_mm": float(
            peak_widths(repeated, [peak], rel_height=0.1)[0][0]
        ),
        "crest_width_80pct_height_mm": float(
            peak_widths(repeated, [peak], rel_height=0.2)[0][0]
        ),
        "half_height_width_mm": float(
            peak_widths(repeated, [peak], rel_height=0.5)[0][0]
        ),
    }
    peak_index = peak % len(profile)
    return {
        "crest_phase_at_theta0_mm": float(phase_mm[peak_index]),
        "crest_from_detrended_mean_mm": float(profile[peak_index]),
        "trough_from_detrended_mean_mm": float(profile[peak_index] - prominence),
        "peak_to_valley_mm": prominence,
        **widths,
    }


def write_plot(path: Path, x: np.ndarray, theta: np.ndarray,
               radius: np.ndarray, branch_mask: np.ndarray,
               result: dict[str, object], phase_mm: np.ndarray,
               profile: np.ndarray) -> None:
    lead = float(result["lead_mm_per_revolution"])
    sign = int(result["sign"])
    metrics = result["profile"]
    crest_phase = float(metrics["crest_phase_at_theta0_mm"])
    deviation = radius - 120.0

    fig, (axis, folded) = plt.subplots(
        2, 1, figsize=(14, 10), gridspec_kw={"height_ratios": [1.7, 1.0]}
    )
    image = axis.pcolormesh(
        x, theta, deviation.T, cmap="coolwarm", vmin=-2.0, vmax=7.0,
        shading="auto",
    )
    fig.colorbar(image, ax=axis, label="Radius - 120 mm [mm]")
    axis.contour(
        x, theta, branch_mask.T.astype(float), levels=[0.5],
        colors="#f6bd60", linewidths=2,
    )
    for turn in range(-6, 9):
        centerline = crest_phase - sign * lead * theta / 360.0 + turn * lead
        visible = (centerline >= x[0]) & (centerline <= x[-1])
        axis.plot(centerline[visible], theta[visible], "--", color="#00a651", lw=1.5)
    axis.set(
        xlabel="Pipe-axis position [mm]",
        ylabel="Axis-corrected angle [deg]",
        title="Measured radial deviation with fitted one-start helix centerlines",
    )
    axis.text(
        0.01, 0.98,
        f"lead = {lead:.1f} mm/rev, dx/dtheta = {lead / 360.0:.3f} mm/deg\n"
        f"{result['handedness']} in model coordinates, fit R^2 = {result['r_squared']:.3f}",
        transform=axis.transAxes, va="top",
        bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "none"},
    )
    axis.grid(alpha=0.15)

    folded.plot(phase_mm, profile, color="#8b1a1a", lw=2)
    folded.axvline(crest_phase, color="#00a651", ls="--", label="crest center")
    prominence = float(metrics["peak_to_valley_mm"])
    crest = float(metrics["crest_from_detrended_mean_mm"])
    for relative_height, key, color, label in (
        (0.1, "crest_width_90pct_height_mm", "#d62728", "90% height"),
        (0.2, "crest_width_80pct_height_mm", "#ff7f0e", "80% height"),
        (0.5, "half_height_width_mm", "#777777", "half height"),
    ):
        folded.axhline(
            crest - prominence * relative_height, color=color, ls=":",
            label=f"{label}: {float(metrics[key]):.1f} mm",
        )
    folded.set(
        xlim=(0.0, lead),
        xlabel="Helical phase along pipe axis [mm]",
        ylabel="Detrended radial deviation [mm]",
        title=(
            f"Folded profile: crest {crest:+.2f} mm / "
            f"trough {float(metrics['trough_from_detrended_mean_mm']):+.2f} mm / "
            f"peak-to-valley {prominence:.2f} mm"
        ),
    )
    folded.grid(alpha=0.25)
    folded.legend(loc="best")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path,
        default=here.parent / "pipe154_auto_scan_m115_115_point_cloud_axis_corrected.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=here / "output")
    parser.add_argument("--lead-min-mm", type=float, default=60.0)
    parser.add_argument("--lead-max-mm", type=float, default=250.0)
    parser.add_argument("--lead-step-mm", type=float, default=0.5)
    parser.add_argument("--harmonics", type=int, default=4)
    parser.add_argument("--branch-center-x-mm", type=float, default=531.0)
    parser.add_argument("--branch-center-angle-deg", type=float, default=5.0)
    parser.add_argument("--branch-exclusion-radius-mm", type=float, default=95.0)
    args = parser.parse_args()

    tracks = load_tracks(args.input.resolve())
    x, theta, radius = common_grid(tracks, axial_step_mm=2.0, smoothing_window=7)
    # Remove additive angular ovality and common axial deformation.  A helical
    # interaction remains because its axial phase shifts with angle.
    residual = radius - np.mean(radius, axis=0, keepdims=True)
    residual = residual - np.mean(residual, axis=1, keepdims=True) + np.mean(residual)
    x_grid = np.broadcast_to(x[:, None], radius.shape)
    theta_grid = np.broadcast_to(theta[None, :], radius.shape)
    branch_arc_mm = 120.0 * np.deg2rad(theta_grid - args.branch_center_angle_deg)
    branch_mask = (
        (x_grid - args.branch_center_x_mm) ** 2 + branch_arc_mm ** 2
        <= args.branch_exclusion_radius_mm ** 2
    )
    valid = (~branch_mask) & (x_grid > x[0] + 12.0) & (x_grid < x[-1] - 15.0)
    best = scan_lead(
        residual[valid], x_grid[valid], np.deg2rad(theta_grid[valid]),
        args.lead_min_mm, args.lead_max_mm, args.lead_step_mm, args.harmonics,
    )
    lead = float(best["lead_mm_per_revolution"])
    sign = int(best["sign"])
    phase_mm, profile = folded_profile(
        residual, x_grid, theta_grid, valid, lead, sign
    )
    metrics = profile_metrics(phase_mm, profile)
    handedness = "right-hand" if sign == -1 else "left-hand"

    result: dict[str, object] = {
        "source_csv": args.input.name,
        "method": "one-start helix Fourier least-squares fit after two-way centering",
        "lead_mm_per_revolution": lead,
        "axial_shift_mm_per_degree": lead / 360.0,
        "handedness": handedness,
        "phase_equation": "phase = 2*pi*x/lead + sign*theta_rad",
        "sign": sign,
        "r_squared": float(best["r_squared"]),
        "harmonics": args.harmonics,
        "profile": metrics,
        "branch_exclusion": {
            "center_x_mm": args.branch_center_x_mm,
            "center_angle_deg": args.branch_center_angle_deg,
            "radius_mm": args.branch_exclusion_radius_mm,
        },
        "measured_range": {
            "x_mm": [float(x[0]), float(x[-1])],
            "theta_deg": [float(theta[0]), float(theta[-1])],
            "angle_tracks": int(len(theta)),
        },
    }
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "helical_ridge_estimation.json"
    plot_path = output_dir / "helical_ridge_estimation.png"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_plot(plot_path, x, theta, radius, branch_mask, result, phase_mm, profile)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"plot={plot_path}")


if __name__ == "__main__":
    main()
