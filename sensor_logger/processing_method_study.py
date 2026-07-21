#!/usr/bin/env python3
"""
Compare dent-center estimates from multiple LK-G preprocessing methods.

Each method writes a processed CSV with lk_out1_corrected_mm replaced by the
method output, then runs the existing point-cloud / axis-correction /
edge-template pipeline at the native 5 deg / 1 mm resolution.
"""

from __future__ import annotations

import argparse
import csv
import math
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter


METHODS = [
    "raw",
    "median_x5",
    "mean_x5",
    "savgol_x7",
    "angle_median3",
    "angle_mean3",
    "median_x5_angle_median3",
    "savgol_x7_angle_mean3",
]


def odd_window(length: int, requested: int) -> int:
    if length <= 1:
        return 1
    window = min(requested, length)
    if window % 2 == 0:
        window -= 1
    return max(window, 1)


def rolling_by_group(df: pd.DataFrame, values: pd.Series, group_col: str, order_col: str, window: int, mode: str) -> pd.Series:
    out = pd.Series(index=df.index, dtype=float)
    tmp = df[[group_col, order_col]].copy()
    tmp["_value"] = values.astype(float)
    for _, group in tmp.sort_values([group_col, order_col]).groupby(group_col, sort=False):
        series = group["_value"]
        if mode == "median":
            filtered = series.rolling(window, center=True, min_periods=1).median()
        elif mode == "mean":
            filtered = series.rolling(window, center=True, min_periods=1).mean()
        else:
            raise ValueError(mode)
        out.loc[group.index] = filtered.to_numpy()
    return out


def savgol_by_angle(df: pd.DataFrame, values: pd.Series, window: int, polyorder: int = 2) -> pd.Series:
    out = pd.Series(index=df.index, dtype=float)
    tmp = df[["angle_deg", "target_delta_mm"]].copy()
    tmp["_value"] = values.astype(float)
    for _, group in tmp.sort_values(["angle_deg", "target_delta_mm"]).groupby("angle_deg", sort=False):
        v = group["_value"].to_numpy(dtype=float)
        w = odd_window(len(v), window)
        if w <= polyorder:
            filtered = v
        else:
            filtered = savgol_filter(v, window_length=w, polyorder=polyorder, mode="interp")
        out.loc[group.index] = filtered
    return out


def build_method_values(df: pd.DataFrame, lk_col: str) -> dict[str, pd.Series]:
    raw = df[lk_col].astype(float)
    median_x5 = rolling_by_group(df, raw, "angle_deg", "target_delta_mm", 5, "median")
    mean_x5 = rolling_by_group(df, raw, "angle_deg", "target_delta_mm", 5, "mean")
    savgol_x7 = savgol_by_angle(df, raw, 7)
    angle_median3 = rolling_by_group(df, raw, "target_delta_mm", "angle_deg", 3, "median")
    angle_mean3 = rolling_by_group(df, raw, "target_delta_mm", "angle_deg", 3, "mean")
    median_x5_angle_median3 = rolling_by_group(df, median_x5, "target_delta_mm", "angle_deg", 3, "median")
    savgol_x7_angle_mean3 = rolling_by_group(df, savgol_x7, "target_delta_mm", "angle_deg", 3, "mean")
    return {
        "raw": raw,
        "median_x5": median_x5,
        "mean_x5": mean_x5,
        "savgol_x7": savgol_x7,
        "angle_median3": angle_median3,
        "angle_mean3": angle_mean3,
        "median_x5_angle_median3": median_x5_angle_median3,
        "savgol_x7_angle_mean3": savgol_x7_angle_mean3,
    }


def write_processed_csv(path: Path, original: pd.DataFrame, values: pd.Series) -> None:
    out = original.copy()
    out["lk_out1_corrected_mm"] = values.astype(float).map(lambda v: f"{v:.6f}")
    out.to_csv(path, index=False, encoding="utf-8-sig", lineterminator="\n")


def read_result(path: Path) -> dict[str, str]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        row = next(reader)
    return row


def fnum(value: object) -> float | None:
    try:
        return float(str(value))
    except Exception:
        return None


def circular_angle_error_deg(angle: float | None, reference: float | None) -> float | None:
    if angle is None or reference is None:
        return None
    return (angle - reference + 180.0) % 360.0 - 180.0


def run_pipeline(script_dir: Path, csv_path: Path, output_dir: Path, args: argparse.Namespace) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script_dir / "resolution_ablation_study.py"),
        "--input",
        str(csv_path),
        "--output-dir",
        str(output_dir),
        "--angle-steps",
        "5",
        "--x-steps",
        "1",
        "--hole-diameter-mm",
        str(args.hole_diameter_mm),
        "--pipe-radius-mm",
        str(args.pipe_radius_mm),
        "--base-radius-mm",
        str(args.base_radius_mm),
        "--axis-section-bin-mm",
        str(args.axis_section_bin_mm),
        "--axis-min-section-points",
        str(args.axis_min_section_points),
    ]
    print("[CMD]", " ".join(cmd))
    subprocess.run(cmd, cwd=str(script_dir), check=True)
    return output_dir / f"{csv_path.stem}_resolution_results.csv"


def write_summary(output_dir: Path, rows: list[dict[str, object]], tolerance_mm: float) -> None:
    csv_path = output_dir / "processing_method_results.csv"
    fields = [
        "method",
        "center_x_zero_mm",
        "center_angle_deg",
        "center_axis_s_mm",
        "center_outward_mm",
        "template_score",
        "combined_score",
        "shift_x_mm",
        "shift_angle_deg",
        "shift_circum_mm",
        "shift_surface_mm",
        "within_tolerance",
        "processed_csv",
        "combined_html",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})

    labels = [str(r["method"]) for r in rows]
    shifts = [float(r.get("shift_surface_mm") or 0.0) for r in rows]
    colors = ["#2f7d32" if bool(r.get("within_tolerance")) else "#c43c35" for r in rows]
    plt.figure(figsize=(12, 5.8))
    plt.plot(range(len(rows)), shifts, marker="o", linewidth=2.6, color="#d24b35")
    plt.axhline(tolerance_mm, color="#555", linestyle="--", linewidth=1.2, label=f"{tolerance_mm:g} mm tolerance")
    plt.scatter(range(len(rows)), shifts, s=70, c=colors, zorder=3)
    plt.xticks(range(len(rows)), labels, rotation=35, ha="right")
    plt.ylabel("center shift from raw [mm]")
    plt.title("Processing method vs estimated center shift")
    plt.grid(axis="y", alpha=0.28)
    plt.legend()
    plt.tight_layout()
    graph_path = output_dir / "processing_method_results.png"
    plt.savefig(graph_path, dpi=180)
    plt.close()

    good = [r for r in rows if bool(r.get("within_tolerance")) and r["method"] != "raw"]
    good_text = ", ".join(str(r["method"]) for r in good) if good else "none"
    table_rows = []
    for row in rows:
        html_link = Path(str(row.get("combined_html", ""))).name
        link = f'<a href="{html_link}">{html_link}</a>' if html_link else ""
        table_rows.append(
            "<tr>"
            f"<td>{row['method']}</td>"
            f"<td>{float(row['center_x_zero_mm']):.2f}</td>"
            f"<td>{float(row['center_angle_deg']):.2f}</td>"
            f"<td>{float(row['shift_surface_mm']):.2f}</td>"
            f"<td>{'yes' if row.get('within_tolerance') else 'no'}</td>"
            f"<td>{float(row['combined_score']):.3f}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )

    html_path = output_dir / "processing_method_results.html"
    html_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Processing Method Study</title>
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 28px; color: #222; }}
.note {{ max-width: 960px; line-height: 1.55; color: #444; }}
img {{ max-width: 100%; border: 1px solid #ddd; }}
table {{ border-collapse: collapse; margin-top: 18px; font-size: 14px; }}
th, td {{ border: 1px solid #ddd; padding: 7px 9px; text-align: right; }}
th {{ background: #f3f3f3; }}
td:first-child, td:last-child {{ text-align: left; }}
</style>
</head>
<body>
<h1>Processing Method Study</h1>
<p class="note">All methods use the same scan data and the same dent-center detection pipeline. The plotted value is the estimated-center shift from the raw method. The current tolerance is {tolerance_mm:g} mm.</p>
<p class="note">Photo-evaluation candidates: {good_text}</p>
<img src="{graph_path.name}" alt="processing method result graph">
<table>
<thead><tr><th>method</th><th>center x mm</th><th>center angle deg</th><th>shift mm</th><th>within tolerance</th><th>score</th><th>detail</th></tr></thead>
<tbody>{''.join(table_rows)}</tbody>
</table>
</body>
</html>
""",
        encoding="utf-8",
    )
    print(f"Wrote CSV: {csv_path}")
    print(f"Wrote graph: {graph_path}")
    print(f"Wrote HTML: {html_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare LK-G preprocessing methods for dent center estimation.")
    parser.add_argument("--input", type=Path, default=Path("pipe154_rescan_resolution_move_v2.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("pipe154_processing_method_study"))
    parser.add_argument("--lk-column", default="lk_out1_mm")
    parser.add_argument("--base-radius-mm", type=float, default=120.0)
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--hole-diameter-mm", type=float, default=154.0)
    parser.add_argument("--axis-section-bin-mm", type=float, default=20.0)
    parser.add_argument("--axis-min-section-points", type=int, default=20)
    parser.add_argument("--tolerance-mm", type=float, default=20.0)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    if not args.input.is_absolute():
        args.input = script_dir / args.input
    if not args.output_dir.is_absolute():
        args.output_dir = script_dir / args.output_dir
    args.output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input, encoding="utf-8-sig")
    required = {"angle_deg", "target_delta_mm", args.lk_column}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Missing columns: {', '.join(sorted(missing))}")
    df = df.dropna(subset=["angle_deg", "target_delta_mm", args.lk_column]).copy()
    df["angle_deg"] = df["angle_deg"].astype(float)
    df["target_delta_mm"] = df["target_delta_mm"].astype(float)
    df[args.lk_column] = df[args.lk_column].astype(float)
    df = df.sort_values(["angle_deg", "target_delta_mm"]).reset_index(drop=True)

    method_values = build_method_values(df, args.lk_column)
    results: list[dict[str, object]] = []

    processed_dir = args.output_dir / "processed_csv"
    processed_dir.mkdir(parents=True, exist_ok=True)
    for method in METHODS:
        print(f"\n[METHOD] {method}")
        processed_csv = processed_dir / f"{args.input.stem}_{method}.csv"
        write_processed_csv(processed_csv, df, method_values[method])
        method_dir = args.output_dir / method
        result_csv = run_pipeline(script_dir, processed_csv, method_dir, args)
        row = read_result(result_csv)
        row_out: dict[str, object] = {
            "method": method,
            "center_x_zero_mm": fnum(row.get("center_x_zero_mm")),
            "center_angle_deg": fnum(row.get("center_angle_deg")),
            "center_axis_s_mm": fnum(row.get("center_axis_s_mm")),
            "center_outward_mm": fnum(row.get("center_outward_mm")),
            "template_score": fnum(row.get("template_score")),
            "combined_score": fnum(row.get("combined_score")),
            "processed_csv": str(processed_csv),
            "combined_html": row.get("combined_html", ""),
        }
        results.append(row_out)

    ref = results[0]
    ref_x = fnum(ref.get("center_x_zero_mm"))
    ref_angle = fnum(ref.get("center_angle_deg"))
    for row in results:
        x = fnum(row.get("center_x_zero_mm"))
        angle = fnum(row.get("center_angle_deg"))
        dx = None if x is None or ref_x is None else x - ref_x
        da = circular_angle_error_deg(angle, ref_angle)
        dc = None if da is None else math.radians(da) * args.pipe_radius_mm
        surface = None
        if dx is not None and dc is not None:
            surface = math.hypot(dx, dc)
        row["shift_x_mm"] = dx
        row["shift_angle_deg"] = da
        row["shift_circum_mm"] = dc
        row["shift_surface_mm"] = surface
        row["within_tolerance"] = bool(surface is not None and surface <= args.tolerance_mm)

    write_summary(args.output_dir, results, args.tolerance_mm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
