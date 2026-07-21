#!/usr/bin/env python3
"""
Compare dent detection methods using one fixed preprocessing result.

Expected input directory:
  pipe154_processing_resolution_matrix/mean_x5

Methods:
  max_depth           deepest outward point
  depth_centroid_p80  weighted centroid of high-depth area
  edge_centroid       weighted centroid of edge-score points
  circular_template   existing circular edge template match
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


METHODS = ["max_depth", "depth_centroid_p80", "edge_centroid", "circular_template"]


def fnum(value: object) -> float | None:
    try:
        return float(str(value))
    except Exception:
        return None


def condition_sort_key(condition: str) -> tuple[float, float]:
    a, x = condition.split("_")
    return (float(a[1:].replace("m", "-")), float(x[1:].replace("m", "-")))


def circular_mean_deg(angles_deg: np.ndarray, weights: np.ndarray) -> float:
    angles_rad = np.deg2rad(angles_deg)
    s = float(np.sum(np.sin(angles_rad) * weights))
    c = float(np.sum(np.cos(angles_rad) * weights))
    return math.degrees(math.atan2(s, c))


def circular_angle_error_deg(angle: float | None, reference: float | None) -> float | None:
    if angle is None or reference is None:
        return None
    return (angle - reference + 180.0) % 360.0 - 180.0


def read_template_results(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def max_depth(axis_csv: Path) -> dict[str, float]:
    df = pd.read_csv(axis_csv, encoding="utf-8-sig")
    df = df.dropna(subset=["x_zero_mm", "angle_deg", "fitted_outward_mm"]).copy()
    row = df.loc[df["fitted_outward_mm"].astype(float).idxmax()]
    return {
        "center_x_zero_mm": float(row["x_zero_mm"]),
        "center_angle_deg": float(row["angle_deg"]),
        "method_score": float(row["fitted_outward_mm"]),
    }


def depth_centroid(axis_csv: Path, percentile: float = 80.0) -> dict[str, float]:
    df = pd.read_csv(axis_csv, encoding="utf-8-sig")
    df = df.dropna(subset=["x_zero_mm", "angle_deg", "fitted_outward_mm"]).copy()
    depth = df["fitted_outward_mm"].astype(float).to_numpy()
    gate = float(np.percentile(depth, percentile))
    weights = np.clip(depth - gate, 0.0, None)
    if float(np.sum(weights)) <= 1e-9:
        weights = np.ones_like(depth)
    x = df["x_zero_mm"].astype(float).to_numpy()
    angle = df["angle_deg"].astype(float).to_numpy()
    return {
        "center_x_zero_mm": float(np.average(x, weights=weights)),
        "center_angle_deg": float(circular_mean_deg(angle, weights)),
        "method_score": float(np.sum(weights)),
    }


def edge_centroid(edge_grid_csv: Path) -> dict[str, float]:
    df = pd.read_csv(edge_grid_csv, encoding="utf-8-sig")
    df = df.dropna(subset=["axis_s_mm", "angle_deg", "edge_score", "is_edge"]).copy()
    axis_min = float(df["axis_s_mm"].astype(float).min())
    edges = df[df["is_edge"].astype(int) > 0].copy()
    if edges.empty:
        edges = df.nlargest(max(5, int(len(df) * 0.01)), "edge_score").copy()
    weights = edges["edge_score"].astype(float).to_numpy()
    weights = np.clip(weights, 1e-9, None)
    x_zero = edges["axis_s_mm"].astype(float).to_numpy() - axis_min
    angle = edges["angle_deg"].astype(float).to_numpy()
    return {
        "center_x_zero_mm": float(np.average(x_zero, weights=weights)),
        "center_angle_deg": float(circular_mean_deg(angle, weights)),
        "method_score": float(np.sum(weights)),
    }


def add_shifts(rows: list[dict[str, object]], pipe_radius_mm: float) -> None:
    ref_by_method: dict[str, dict[str, object]] = {}
    for row in rows:
        if row["condition"] == "a5_x1":
            ref_by_method[str(row["method"])] = row
    for row in rows:
        ref = ref_by_method.get(str(row["method"]))
        if not ref:
            continue
        x = fnum(row.get("center_x_zero_mm"))
        angle = fnum(row.get("center_angle_deg"))
        ref_x = fnum(ref.get("center_x_zero_mm"))
        ref_angle = fnum(ref.get("center_angle_deg"))
        dx = None if x is None or ref_x is None else x - ref_x
        da = circular_angle_error_deg(angle, ref_angle)
        dc = None if da is None else math.radians(da) * pipe_radius_mm
        surface = None
        if dx is not None and dc is not None:
            surface = math.hypot(dx, dc)
        row["shift_x_mm"] = dx
        row["shift_angle_deg"] = da
        row["shift_circum_mm"] = dc
        row["shift_surface_mm"] = surface


def write_outputs(output_dir: Path, rows: list[dict[str, object]], tolerance_mm: float) -> None:
    csv_path = output_dir / "detection_method_results.csv"
    fields = [
        "method",
        "condition",
        "angle_step_deg",
        "x_step_mm",
        "center_x_zero_mm",
        "center_angle_deg",
        "method_score",
        "shift_surface_mm",
        "within_tolerance",
    ]
    for row in rows:
        shift = fnum(row.get("shift_surface_mm"))
        row["within_tolerance"] = bool(shift is not None and shift <= tolerance_mm)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

    conditions = sorted({str(r["condition"]) for r in rows}, key=condition_sort_key)
    by_method = {m: {str(r["condition"]): r for r in rows if r["method"] == m} for m in METHODS}
    graph_path = output_dir / "detection_method_lines.png"
    plt.figure(figsize=(18, 7))
    for method in METHODS:
        y = []
        for condition in conditions:
            row = by_method.get(method, {}).get(condition)
            y.append(float("nan") if row is None or fnum(row.get("shift_surface_mm")) is None else float(row["shift_surface_mm"]))
        plt.plot(range(len(conditions)), y, marker="o", linewidth=2.1, label=method)
    plt.axhline(tolerance_mm, color="#333", linestyle="--", linewidth=1.2, label=f"{tolerance_mm:g} mm tolerance")
    plt.xticks(range(len(conditions)), conditions, rotation=55, ha="right")
    plt.ylabel("center shift from each method's a5_x1 [mm]")
    plt.title("Detection method comparison with mean_x5 preprocessing")
    plt.grid(axis="y", alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(graph_path, dpi=180)
    plt.close()

    best_rows = []
    for condition in conditions:
        candidates = [r for r in rows if r["condition"] == condition and fnum(r.get("shift_surface_mm")) is not None]
        candidates.sort(key=lambda r: float(r["shift_surface_mm"]))
        best = candidates[0]
        best_rows.append(
            f"<tr><td>{condition}</td><td>{best['method']}</td><td>{float(best['shift_surface_mm']):.2f}</td></tr>"
        )

    table_rows = []
    for row in rows:
        table_rows.append(
            "<tr>"
            f"<td>{row['method']}</td>"
            f"<td>{row['condition']}</td>"
            f"<td>{row['angle_step_deg']}</td>"
            f"<td>{row['x_step_mm']}</td>"
            f"<td>{float(row['center_x_zero_mm']):.2f}</td>"
            f"<td>{float(row['center_angle_deg']):.2f}</td>"
            f"<td>{float(row['shift_surface_mm']):.2f}</td>"
            f"<td>{'yes' if row['within_tolerance'] else 'no'}</td>"
            "</tr>"
        )

    html_path = output_dir / "detection_method_results.html"
    html_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Detection Method Study</title>
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 28px; color: #222; }}
.note {{ max-width: 1060px; line-height: 1.55; color: #444; }}
img {{ max-width: 100%; border: 1px solid #ddd; }}
table {{ border-collapse: collapse; margin-top: 18px; font-size: 13px; }}
th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: right; }}
th {{ background: #f3f3f3; }}
td:first-child, td:nth-child(2) {{ text-align: left; }}
</style>
</head>
<body>
<h1>Detection Method Study</h1>
<p class="note">Preprocessing is fixed to mean_x5. This page compares four dent-center detection methods. The plotted value is the center shift from each method's own a5_x1 result. The tolerance line is {tolerance_mm:g} mm.</p>
<img src="{graph_path.name}" alt="detection method line graph">
<h2>Best method per condition</h2>
<table><thead><tr><th>condition</th><th>best method</th><th>shift mm</th></tr></thead><tbody>{''.join(best_rows)}</tbody></table>
<h2>All results</h2>
<table><thead><tr><th>method</th><th>condition</th><th>angle step</th><th>x step</th><th>center x</th><th>center angle</th><th>shift mm</th><th>within 20mm</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table>
</body>
</html>
""",
        encoding="utf-8",
    )
    print(f"Wrote CSV: {csv_path}")
    print(f"Wrote graph: {graph_path}")
    print(f"Wrote HTML: {html_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare dent detection methods with fixed preprocessing.")
    parser.add_argument("--method-dir", type=Path, default=Path("pipe154_processing_resolution_matrix") / "mean_x5")
    parser.add_argument("--output-dir", type=Path, default=Path("pipe154_detection_method_study_mean_x5"))
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--tolerance-mm", type=float, default=20.0)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    if not args.method_dir.is_absolute():
        args.method_dir = script_dir / args.method_dir
    if not args.output_dir.is_absolute():
        args.output_dir = script_dir / args.output_dir
    args.output_dir.mkdir(parents=True, exist_ok=True)

    template_csv = next(args.method_dir.glob("*_resolution_results.csv"))
    template_rows = read_template_results(template_csv)
    rows: list[dict[str, object]] = []
    for tr in template_rows:
        condition = str(tr["condition"])
        prefix = args.method_dir / f"pipe154_rescan_resolution_move_v2_mean_x5_{condition}"
        axis_csv = prefix.with_name(prefix.name + "_point_cloud_axis_corrected.csv")
        edge_grid_csv = prefix.with_name(prefix.name + "_axis_edge_grid_p97.csv")
        common = {
            "condition": condition,
            "angle_step_deg": tr.get("angle_step_deg", ""),
            "x_step_mm": tr.get("x_step_mm", ""),
        }

        for method, result in [
            ("max_depth", max_depth(axis_csv)),
            ("depth_centroid_p80", depth_centroid(axis_csv, 80.0)),
            ("edge_centroid", edge_centroid(edge_grid_csv)),
            (
                "circular_template",
                {
                    "center_x_zero_mm": float(tr["center_x_zero_mm"]),
                    "center_angle_deg": float(tr["center_angle_deg"]),
                    "method_score": float(tr.get("combined_score") or 0.0),
                },
            ),
        ]:
            out = dict(common)
            out["method"] = method
            out.update(result)
            rows.append(out)

    add_shifts(rows, args.pipe_radius_mm)
    write_outputs(args.output_dir, rows, args.tolerance_mm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
