#!/usr/bin/env python3
"""Compare rigid-wheel centre paths over the symmetric triangular ridge."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from scipy.ndimage import grey_dilation


RIDGE_HEIGHT_MM = 1.5122259907957454
RIDGE_TOTAL_WIDTH_MM = 30.0
RIDGE_LEAD_MM = 189.5
GRID_STEP_MM = 0.02
WHEEL_DIAMETERS_MM = (10.0, 15.0, 20.0, 25.0, 32.0, 40.0, 54.0, 70.0, 100.0)


def triangular_ridge(x_mm: np.ndarray) -> np.ndarray:
    distance = np.minimum(x_mm, RIDGE_LEAD_MM - x_mm)
    return np.maximum(
        0.0,
        RIDGE_HEIGHT_MM * (1.0 - distance / (0.5 * RIDGE_TOTAL_WIDTH_MM)),
    )


def wheel_centre_lift(surface_mm: np.ndarray, radius_mm: float) -> np.ndarray:
    count = int(np.ceil(radius_mm / GRID_STEP_MM))
    offsets = np.arange(-count, count + 1) * GRID_STEP_MM
    circular_offset = np.sqrt(
        np.maximum(0.0, radius_mm * radius_mm - offsets * offsets)
    )
    return (
        grey_dilation(surface_mm, structure=circular_offset, mode="wrap")
        - radius_mm
    )


def main() -> None:
    here = Path(__file__).resolve().parent
    output = here / "output"
    x = np.arange(0.0, RIDGE_LEAD_MM, GRID_STEP_MM)
    surface = triangular_ridge(x)
    rows = []
    profiles: dict[float, np.ndarray] = {}
    for diameter in WHEEL_DIAMETERS_MM:
        centre = wheel_centre_lift(surface, 0.5 * diameter)
        profiles[diameter] = centre
        slope = np.gradient(centre, GRID_STEP_MM)
        maximum_slope = float(np.max(np.abs(slope)))
        rows.append({
            "wheel_diameter_mm": diameter,
            "wheel_radius_mm": 0.5 * diameter,
            "ridge_height_to_radius": RIDGE_HEIGHT_MM / (0.5 * diameter),
            "maximum_centre_path_slope": maximum_slope,
            "maximum_centre_path_angle_deg": float(
                np.degrees(np.arctan(maximum_slope))
            ),
            "ridge_force_per_100n_normal_n": 100.0 * maximum_slope,
            "lifted_span_above_1pct_mm": float(
                np.count_nonzero(centre > 0.01 * RIDGE_HEIGHT_MM) * GRID_STEP_MM
            ),
            "lifted_span_above_10pct_mm": float(
                np.count_nonzero(centre > 0.10 * RIDGE_HEIGHT_MM) * GRID_STEP_MM
            ),
            "lifted_span_above_50pct_mm": float(
                np.count_nonzero(centre > 0.50 * RIDGE_HEIGHT_MM) * GRID_STEP_MM
            ),
        })

    result = {
        "method": "periodic morphological offset of a symmetric triangular ridge by each rigid-wheel radius",
        "ridge_height_mm": RIDGE_HEIGHT_MM,
        "ridge_total_width_mm": RIDGE_TOTAL_WIDTH_MM,
        "ridge_lead_mm": RIDGE_LEAD_MM,
        "grid_step_mm": GRID_STEP_MM,
        "interpretation": [
            "A straight ramp has the same maximum centre-path slope for every rigid wheel that can contact the ramp tangentially.",
            "Larger wheels begin lifting earlier and remain lifted over a longer axial span.",
            "Diameter effects become dominant for vertical steps, rounded short ramps, dynamics, deformation, and bearing torque; those are outside this calculation.",
        ],
        "diameters": rows,
    }
    json_path = output / "wheel_diameter_ridge_profile_comparison.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    csv_path = output / "wheel_diameter_ridge_profile_comparison.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    signed_x = np.where(x <= 0.5 * RIDGE_LEAD_MM, x, x - RIDGE_LEAD_MM)
    order = np.argsort(signed_x)
    view = np.abs(signed_x[order]) <= 24.0
    figure, axes = plt.subplots(2, 1, figsize=(12, 8))
    axes[0].plot(
        signed_x[order][view], surface[order][view], "k--", lw=2,
        label="ridge surface",
    )
    for diameter in (10.0, 25.0, 32.0, 54.0, 100.0):
        axes[0].plot(
            signed_x[order][view], profiles[diameter][order][view],
            label=f"wheel centre lift, D={diameter:g} mm",
        )
    axes[0].set(
        xlabel="Axial position from ridge crest [mm]",
        ylabel="Lift above flat surface [mm]",
        title="Rigid-wheel centre paths over a 30 mm symmetric ridge",
    )
    axes[0].grid(alpha=0.25)
    axes[0].legend(ncol=2)

    diameters = np.array([row["wheel_diameter_mm"] for row in rows])
    force_ratio = np.round(np.array([
        row["ridge_force_per_100n_normal_n"] for row in rows
    ]), 6)
    lifted_span = np.array([
        row["lifted_span_above_1pct_mm"] for row in rows
    ])
    axes[1].plot(
        diameters, force_ratio, "o-", color="#1665a8",
        label="maximum ridge force per 100 N normal load",
    )
    axes[1].set(
        xlabel="Wheel diameter [mm]",
        ylabel="Maximum ridge force per 100 N [N]",
        xticks=diameters,
    )
    axes[1].set_ylim(force_ratio[0] - 0.25, force_ratio[0] + 0.25)
    axes[1].grid(alpha=0.25)
    second = axes[1].twinx()
    second.plot(
        diameters, lifted_span, "s--", color="#d67b16",
        label="axial span lifted above 1% height",
    )
    second.set_ylabel("Lifted axial span [mm]")
    lines = axes[1].lines + second.lines
    axes[1].legend(lines, [line.get_label() for line in lines], loc="center right")
    figure.tight_layout()
    figure_path = output / "wheel_diameter_ridge_profile_comparison.png"
    figure.savefig(figure_path, dpi=180)
    plt.close(figure)
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"output={json_path}")
    print(f"output={csv_path}")
    print(f"output={figure_path}")


if __name__ == "__main__":
    main()
