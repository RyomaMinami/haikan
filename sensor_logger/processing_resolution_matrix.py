#!/usr/bin/env python3
"""
Compare preprocessing methods across multiple angle/x resolutions.

This uses processing_method_study.py to create method-specific processed CSVs,
then runs resolution_ablation_study.py for each method over the requested
resolution grid. Finally it writes a method x condition summary table and HTML.
"""

from __future__ import annotations

import argparse
import csv
import math
import subprocess
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from processing_method_study import METHODS, build_method_values, write_processed_csv


def parse_list(text: str) -> list[float]:
    values = []
    for part in text.split(","):
        part = part.strip()
        if part:
            values.append(float(part))
    if not values:
        raise argparse.ArgumentTypeError("empty list")
    return values


def fnum(value: object) -> float | None:
    try:
        return float(str(value))
    except Exception:
        return None


def condition_name(angle_step: float, x_step: float) -> str:
    a = ("%g" % angle_step).replace("-", "m").replace(".", "p")
    x = ("%g" % x_step).replace("-", "m").replace(".", "p")
    return f"a{a}_x{x}"


def circular_angle_error_deg(angle: float | None, reference: float | None) -> float | None:
    if angle is None or reference is None:
        return None
    return (angle - reference + 180.0) % 360.0 - 180.0


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def run_resolution_study(
    script_dir: Path,
    processed_csv: Path,
    output_dir: Path,
    args: argparse.Namespace,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    result_csv = output_dir / f"{processed_csv.stem}_resolution_results.csv"
    if result_csv.exists() and not args.force:
        print(f"[SKIP] {result_csv}")
        return result_csv

    cmd = [
        sys.executable,
        str(script_dir / "resolution_ablation_study.py"),
        "--input",
        str(processed_csv),
        "--output-dir",
        str(output_dir),
        "--angle-steps",
        args.angle_steps,
        "--x-steps",
        args.x_steps,
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
    return result_csv


def add_reference_errors(rows: list[dict[str, object]], pipe_radius_mm: float) -> None:
    by_method = {}
    for row in rows:
        if row["condition"] == "a5_x1":
            by_method[row["method"]] = row

    for row in rows:
        ref = by_method.get(row["method"])
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


def write_outputs(output_dir: Path, rows: list[dict[str, object]], angle_steps: list[float], x_steps: list[float], tolerance_mm: float) -> None:
    summary_csv = output_dir / "processing_resolution_matrix_results.csv"
    fields = [
        "method",
        "condition",
        "angle_step_deg",
        "x_step_mm",
        "raw_rows",
        "center_x_zero_mm",
        "center_angle_deg",
        "combined_score",
        "shift_surface_mm",
        "within_tolerance",
        "combined_html",
    ]
    with summary_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            row["within_tolerance"] = bool((fnum(row.get("shift_surface_mm")) or 0.0) <= tolerance_mm)
            writer.writerow({field: row.get(field, "") for field in fields})

    conditions = [condition_name(a, x) for a in angle_steps for x in x_steps]
    method_rows = {m: {str(r["condition"]): r for r in rows if r["method"] == m} for m in METHODS}

    graph_path = output_dir / "processing_resolution_matrix_lines.png"
    plt.figure(figsize=(18, 7))
    for method in METHODS:
        y = []
        for condition in conditions:
            row = method_rows.get(method, {}).get(condition)
            y.append(float("nan") if row is None or fnum(row.get("shift_surface_mm")) is None else float(row["shift_surface_mm"]))
        plt.plot(range(len(conditions)), y, marker="o", linewidth=1.8, label=method)
    plt.axhline(tolerance_mm, color="#333", linestyle="--", linewidth=1.2, label=f"{tolerance_mm:g} mm tolerance")
    plt.xticks(range(len(conditions)), conditions, rotation=55, ha="right")
    plt.ylabel("center shift from each method's a5_x1 [mm]")
    plt.title("Resolution and preprocessing method comparison")
    plt.grid(axis="y", alpha=0.25)
    plt.legend(ncol=2, fontsize=9)
    plt.tight_layout()
    plt.savefig(graph_path, dpi=180)
    plt.close()

    rank_rows = []
    for condition in conditions:
        candidates = [r for r in rows if r["condition"] == condition and fnum(r.get("shift_surface_mm")) is not None]
        candidates.sort(key=lambda r: (float(r["shift_surface_mm"]), -float(r.get("combined_score") or 0.0)))
        if candidates:
            best = candidates[0]
            rank_rows.append(
                f"<tr><td>{condition}</td><td>{best['method']}</td><td>{float(best['shift_surface_mm']):.2f}</td><td>{float(best.get('combined_score') or 0.0):.3f}</td></tr>"
            )

    table_rows = []
    for row in rows:
        detail = Path(str(row.get("combined_html", ""))).name
        link = f'<a href="{row["method"]}/{detail}">{detail}</a>' if detail else ""
        table_rows.append(
            "<tr>"
            f"<td>{row['method']}</td>"
            f"<td>{row['condition']}</td>"
            f"<td>{row['angle_step_deg']}</td>"
            f"<td>{row['x_step_mm']}</td>"
            f"<td>{float(row['center_x_zero_mm']):.2f}</td>"
            f"<td>{float(row['center_angle_deg']):.2f}</td>"
            f"<td>{float(row['shift_surface_mm']):.2f}</td>"
            f"<td>{'yes' if row.get('within_tolerance') else 'no'}</td>"
            f"<td>{float(row.get('combined_score') or 0.0):.3f}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )

    html_path = output_dir / "processing_resolution_matrix_results.html"
    html_path.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Processing x Resolution Matrix</title>
<style>
body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 28px; color: #222; }}
.note {{ max-width: 1060px; line-height: 1.55; color: #444; }}
img {{ max-width: 100%; border: 1px solid #ddd; }}
table {{ border-collapse: collapse; margin-top: 18px; font-size: 13px; }}
th, td {{ border: 1px solid #ddd; padding: 6px 8px; text-align: right; }}
th {{ background: #f3f3f3; }}
td:first-child, td:nth-child(2), td:last-child {{ text-align: left; }}
</style>
</head>
<body>
<h1>Processing x Resolution Matrix</h1>
<p class="note">Each preprocessing method is evaluated from a5_x1 through all requested resolution conditions. The y-value is the center shift from that method's own a5_x1 result. The tolerance line is {tolerance_mm:g} mm.</p>
<img src="{graph_path.name}" alt="method resolution line graph">
<h2>Best method per condition</h2>
<table><thead><tr><th>condition</th><th>best method</th><th>shift mm</th><th>score</th></tr></thead><tbody>{''.join(rank_rows)}</tbody></table>
<h2>All results</h2>
<table><thead><tr><th>method</th><th>condition</th><th>angle step</th><th>x step</th><th>center x</th><th>center angle</th><th>shift mm</th><th>within 20mm</th><th>score</th><th>detail</th></tr></thead><tbody>{''.join(table_rows)}</tbody></table>
</body>
</html>
""",
        encoding="utf-8",
    )
    print(f"Wrote CSV: {summary_csv}")
    print(f"Wrote graph: {graph_path}")
    print(f"Wrote HTML: {html_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare preprocessing methods across all resolution conditions.")
    parser.add_argument("--input", type=Path, default=Path("pipe154_rescan_resolution_move_v2.csv"))
    parser.add_argument("--output-dir", type=Path, default=Path("pipe154_processing_resolution_matrix"))
    parser.add_argument("--lk-column", default="lk_out1_mm")
    parser.add_argument("--angle-steps", default="5,10,15,20,25,30")
    parser.add_argument("--x-steps", default="1,2,5,10,15,20")
    parser.add_argument("--base-radius-mm", type=float, default=120.0)
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--hole-diameter-mm", type=float, default=154.0)
    parser.add_argument("--axis-section-bin-mm", type=float, default=60.0)
    parser.add_argument("--axis-min-section-points", type=int, default=5)
    parser.add_argument("--tolerance-mm", type=float, default=20.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    if not args.input.is_absolute():
        args.input = script_dir / args.input
    if not args.output_dir.is_absolute():
        args.output_dir = script_dir / args.output_dir
    args.output_dir.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(args.input, encoding="utf-8-sig")
    df = df.dropna(subset=["angle_deg", "target_delta_mm", args.lk_column]).copy()
    df["angle_deg"] = df["angle_deg"].astype(float)
    df["target_delta_mm"] = df["target_delta_mm"].astype(float)
    df[args.lk_column] = df[args.lk_column].astype(float)
    df = df.sort_values(["angle_deg", "target_delta_mm"]).reset_index(drop=True)
    values = build_method_values(df, args.lk_column)

    processed_dir = args.output_dir / "processed_csv"
    processed_dir.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict[str, object]] = []
    for method in METHODS:
        print(f"\n[METHOD] {method}")
        processed_csv = processed_dir / f"{args.input.stem}_{method}.csv"
        if args.force or not processed_csv.exists():
            write_processed_csv(processed_csv, df, values[method])
        result_csv = run_resolution_study(script_dir, processed_csv, args.output_dir / method, args)
        for row in read_rows(result_csv):
            out: dict[str, object] = dict(row)
            out["method"] = method
            all_rows.append(out)

    add_reference_errors(all_rows, args.pipe_radius_mm)
    write_outputs(args.output_dir, all_rows, parse_list(args.angle_steps), parse_list(args.x_steps), args.tolerance_mm)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
