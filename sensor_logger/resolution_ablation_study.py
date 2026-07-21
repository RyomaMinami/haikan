#!/usr/bin/env python3
"""
Resolution ablation study for pipe dent detection.

This script takes a high-density angle/wheel scan CSV, downsamples it as if it
had been measured with coarser angle and axial intervals, then runs the same
analysis pipeline for each condition.

The first requested condition is used as the reference unless
--reference-summary is supplied. A typical reference is 5 deg / 1 mm.
"""

from __future__ import annotations

import argparse
import csv
import html
import math
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Iterable


def parse_number_list(text: str, cast):
    values = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        values.append(cast(part))
    if not values:
        raise argparse.ArgumentTypeError("list must contain at least one value")
    return values


def fnum(value: str | None, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def fmt_float(value: float | None, digits: int = 3) -> str:
    if value is None or not math.isfinite(value):
        return ""
    return f"{value:.{digits}f}"


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def write_rows(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def unique_sorted(values: Iterable[float], ndigits: int = 6) -> list[float]:
    return sorted({round(v, ndigits) for v in values if math.isfinite(v)})


def keep_angle(angle: float, first_angle: float, angle_step: float, tol: float = 1e-4) -> bool:
    offset = angle - first_angle
    n = round(offset / angle_step)
    return abs(offset - n * angle_step) <= tol


def downsample_rows(
    rows: list[dict[str, str]],
    angle_step: float,
    x_step: float,
    angle_column: str,
    x_column: str,
) -> list[dict[str, str]]:
    numeric_angles = [fnum(r.get(angle_column), math.nan) for r in rows]
    angles = unique_sorted([v for v in numeric_angles if v is not None])
    if not angles:
        raise ValueError(f"no numeric angle values in column {angle_column}")
    first_angle = min(angles)

    selected: list[dict[str, str]] = []
    seen_keys: set[tuple[float, int]] = set()
    first_x_by_angle: dict[float, float] = {}

    for row in rows:
        angle = fnum(row.get(angle_column), math.nan)
        x = fnum(row.get(x_column), math.nan)
        if angle is None or x is None or not math.isfinite(angle) or not math.isfinite(x):
            continue
        angle_key = round(angle, 6)
        if not keep_angle(angle_key, first_angle, angle_step):
            continue

        if angle_key not in first_x_by_angle:
            first_x_by_angle[angle_key] = x
        x0 = first_x_by_angle[angle_key]
        index_float = (x - x0) / x_step
        index = round(index_float)
        if abs(index_float - index) > 1e-4:
            continue
        key = (angle_key, index)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        selected.append(row)

    return selected


def run_command(cmd: list[str], cwd: Path, dry_run: bool = False) -> None:
    shown = " ".join(str(c) for c in cmd)
    print(f"[CMD] {shown}")
    if dry_run:
        return
    subprocess.run(cmd, cwd=str(cwd), check=True)


def read_grid_min_axis(path: Path) -> float | None:
    if not path.exists():
        return None
    mins: list[float] = []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            value = fnum(row.get("axis_s_mm"))
            if value is not None and math.isfinite(value):
                mins.append(value)
    return min(mins) if mins else None


def read_match_summary(path: Path, grid_path: Path) -> dict[str, str | float | None]:
    if not path.exists():
        return {}
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        row = next(reader, None)
    if row is None:
        return {}

    center_axis_s = fnum(row.get("center_axis_s_mm"))
    min_axis = read_grid_min_axis(grid_path)
    center_x_zero = None
    if center_axis_s is not None and min_axis is not None:
        center_x_zero = center_axis_s - min_axis

    out: dict[str, str | float | None] = dict(row)
    out["center_axis_s_mm"] = center_axis_s
    out["center_x_zero_mm"] = center_x_zero
    out["center_angle_deg"] = fnum(row.get("center_angle_deg"))
    out["template_score"] = fnum(row.get("template_score"))
    out["combined_score"] = fnum(row.get("combined_score"))
    out["center_outward_mm"] = fnum(row.get("center_outward_mm"))
    return out


def median_x_count_by_angle(rows: list[dict[str, str]], angle_column: str, x_column: str) -> int:
    counts: dict[float, set[float]] = {}
    for row in rows:
        angle = fnum(row.get(angle_column))
        x = fnum(row.get(x_column))
        if angle is None or x is None:
            continue
        counts.setdefault(round(angle, 6), set()).add(round(x, 6))
    if not counts:
        return 0
    return int(round(statistics.median(len(v) for v in counts.values())))


def circular_angle_error_deg(angle: float | None, reference: float | None) -> float | None:
    if angle is None or reference is None:
        return None
    return (angle - reference + 180.0) % 360.0 - 180.0


def write_result_csv(path: Path, results: list[dict[str, object]]) -> None:
    fields = [
        "condition",
        "angle_step_deg",
        "x_step_mm",
        "raw_rows",
        "angle_count",
        "median_x_count",
        "center_x_zero_mm",
        "center_angle_deg",
        "center_axis_s_mm",
        "center_outward_mm",
        "template_score",
        "combined_score",
        "error_x_zero_mm",
        "error_angle_deg",
        "error_circum_mm",
        "error_surface_mm",
        "downsampled_csv",
        "combined_html",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in results:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_result_html(path: Path, results: list[dict[str, object]], title: str, reference: dict[str, object]) -> None:
    rows_html = []
    for row in results:
        html_path = str(row.get("combined_html", ""))
        link = html.escape(Path(html_path).name) if html_path else ""
        if html_path:
            link = f'<a href="{html.escape(Path(html_path).name)}">{link}</a>'
        rows_html.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('condition', '')))}</td>"
            f"<td>{html.escape(str(row.get('angle_step_deg', '')))}</td>"
            f"<td>{html.escape(str(row.get('x_step_mm', '')))}</td>"
            f"<td>{html.escape(str(row.get('raw_rows', '')))}</td>"
            f"<td>{fmt_float(row.get('center_x_zero_mm'), 2)}</td>"
            f"<td>{fmt_float(row.get('center_angle_deg'), 2)}</td>"
            f"<td>{fmt_float(row.get('error_x_zero_mm'), 2)}</td>"
            f"<td>{fmt_float(row.get('error_angle_deg'), 2)}</td>"
            f"<td>{fmt_float(row.get('error_surface_mm'), 2)}</td>"
            f"<td>{fmt_float(row.get('combined_score'), 3)}</td>"
            f"<td>{link}</td>"
            "</tr>"
        )

    html_text = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)}</title>
  <style>
    body {{ font-family: Arial, "Meiryo", sans-serif; margin: 24px; color: #202124; }}
    h1 {{ font-size: 24px; margin: 0 0 12px; }}
    h2 {{ font-size: 18px; margin: 24px 0 8px; }}
    p {{ line-height: 1.6; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: right; }}
    th:first-child, td:first-child, th:last-child, td:last-child {{ text-align: left; }}
    th {{ background: #f6f8fa; position: sticky; top: 0; }}
    .note {{ background: #fff8c5; border: 1px solid #d4a72c; padding: 10px 12px; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>{html.escape(title)}</h1>
  <p class="note">
    高密度CSVを間引いて、測定分解能がくぼみ中心推定に与える影響を比較しています。
    誤差は基準条件との差です。基準中心:
    x={fmt_float(reference.get('center_x_zero_mm'), 2)} mm,
    angle={fmt_float(reference.get('center_angle_deg'), 2)} deg。
  </p>
  <h2>Resolution Comparison</h2>
  <table>
    <thead>
      <tr>
        <th>condition</th>
        <th>angle step deg</th>
        <th>x step mm</th>
        <th>rows</th>
        <th>center x mm</th>
        <th>center angle deg</th>
        <th>error x mm</th>
        <th>error angle deg</th>
        <th>surface error mm</th>
        <th>score</th>
        <th>detail</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows_html)}
    </tbody>
  </table>
</body>
</html>
"""
    path.write_text(html_text, encoding="utf-8")


def build_condition_prefix(input_path: Path, out_dir: Path, angle_step: float, x_step: float) -> Path:
    a = ("%g" % angle_step).replace(".", "p").replace("-", "m")
    x = ("%g" % x_step).replace(".", "p").replace("-", "m")
    return out_dir / f"{input_path.stem}_a{a}_x{x}"


def process_condition(
    args: argparse.Namespace,
    script_dir: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
    angle_step: float,
    x_step: float,
) -> dict[str, object]:
    prefix = build_condition_prefix(args.input, args.output_dir, angle_step, x_step)
    condition = prefix.name.replace(args.input.stem + "_", "")
    downsampled_csv = prefix.with_suffix(".csv")

    subset = downsample_rows(rows, angle_step, x_step, args.angle_column, args.x_column)
    if len(subset) < args.min_rows:
        raise RuntimeError(f"{condition}: only {len(subset)} rows after downsampling")
    write_rows(downsampled_csv, fieldnames, subset)

    points_csv = prefix.with_name(prefix.name + "_point_cloud.csv")
    surface_html = prefix.with_name(prefix.name + "_pipe_surface.html")
    axis_csv = prefix.with_name(prefix.name + "_point_cloud_axis_corrected.csv")
    centers_csv = prefix.with_name(prefix.name + "_point_cloud_axis_centers.csv")
    axis_html = prefix.with_name(prefix.name + "_axis_corrected.html")
    edge_grid_csv = prefix.with_name(prefix.name + f"_axis_edge_grid_p{int(args.edge_percentile)}.csv")
    edges_csv = prefix.with_name(prefix.name + f"_axis_edges_p{int(args.edge_percentile)}.csv")
    edges_html = prefix.with_name(prefix.name + f"_axis_edges_p{int(args.edge_percentile)}.html")
    summary_csv = prefix.with_name(prefix.name + "_circular_edge_match_summary.csv")
    boundary_csv = prefix.with_name(prefix.name + "_circular_edge_boundary.csv")
    match_html = prefix.with_name(prefix.name + "_circular_edge_match.html")
    dent_region_csv = prefix.with_name(prefix.name + "_detected_dent_region.csv")
    dent_region_html = prefix.with_name(prefix.name + "_detected_dent_region.html")
    combined_html = prefix.with_name(prefix.name + "_combined_analysis.html")

    if summary_csv.exists() and combined_html.exists() and not args.dry_run:
        print(f"[SKIP CONDITION] {condition}: existing analysis found")
        summary = read_match_summary(summary_csv, edge_grid_csv)
        angle_count = len(unique_sorted(fnum(r.get(args.angle_column), math.nan) for r in subset))
        return {
            "condition": condition,
            "angle_step_deg": angle_step,
            "x_step_mm": x_step,
            "raw_rows": len(subset),
            "angle_count": angle_count,
            "median_x_count": median_x_count_by_angle(subset, args.angle_column, args.x_column),
            "center_x_zero_mm": summary.get("center_x_zero_mm"),
            "center_angle_deg": summary.get("center_angle_deg"),
            "center_axis_s_mm": summary.get("center_axis_s_mm"),
            "center_outward_mm": summary.get("center_outward_mm"),
            "template_score": summary.get("template_score"),
            "combined_score": summary.get("combined_score"),
            "downsampled_csv": str(downsampled_csv),
            "combined_html": str(combined_html),
        }

    py = sys.executable
    run_command(
        [
            py,
            "pipe_surface_visualizer.py",
            "--input",
            str(downsampled_csv),
            "--output-html",
            str(surface_html),
            "--output-points",
            str(points_csv),
            "--base-radius-mm",
            str(args.base_radius_mm),
            "--x-column",
            args.x_column,
            "--invert-lk",
        ],
        script_dir,
        args.dry_run,
    )
    run_command(
        [
            py,
            "axis_correct_point_cloud.py",
            "--input",
            str(points_csv),
            "--output-csv",
            str(axis_csv),
            "--output-centers",
            str(centers_csv),
            "--output-html",
            str(axis_html),
            "--section-bin-mm",
            str(args.axis_section_bin_mm),
            "--min-section-points",
            str(args.axis_min_section_points),
        ],
        script_dir,
        args.dry_run,
    )
    run_command(
        [
            py,
            "axis_edge_detect.py",
            "--input",
            str(axis_csv),
            "--output-edges",
            str(edges_csv),
            "--output-grid",
            str(edge_grid_csv),
            "--output-html",
            str(edges_html),
            "--edge-percentile",
            str(args.edge_percentile),
        ],
        script_dir,
        args.dry_run,
    )
    run_command(
        [
            py,
            "axis_edge_template_match.py",
            "--input-grid",
            str(edge_grid_csv),
            "--hole-diameter-mm",
            str(args.hole_diameter_mm),
            "--pipe-radius-mm",
            str(args.pipe_radius_mm),
            "--output-summary",
            str(summary_csv),
            "--output-boundary",
            str(boundary_csv),
            "--output-html",
            str(match_html),
        ],
        script_dir,
        args.dry_run,
    )
    run_command(
        [
            py,
            "axis_dent_region_from_match.py",
            "--points",
            str(axis_csv),
            "--summary",
            str(summary_csv),
            "--output-csv",
            str(dent_region_csv),
            "--output-html",
            str(dent_region_html),
        ],
        script_dir,
        args.dry_run,
    )
    run_command(
        [
            py,
            "combined_pipe_analysis_view.py",
            "--raw-point-cloud",
            str(points_csv),
            "--axis-point-cloud",
            str(axis_csv),
            "--edge-grid",
            str(edge_grid_csv),
            "--boundary",
            str(boundary_csv),
            "--summary",
            str(summary_csv),
            "--dent-region",
            str(dent_region_csv),
            "--output",
            str(combined_html),
            "--title",
            f"Resolution study {condition}",
        ],
        script_dir,
        args.dry_run,
    )

    summary = read_match_summary(summary_csv, edge_grid_csv) if not args.dry_run else {}
    angle_count = len(unique_sorted(fnum(r.get(args.angle_column), math.nan) for r in subset))
    return {
        "condition": condition,
        "angle_step_deg": angle_step,
        "x_step_mm": x_step,
        "raw_rows": len(subset),
        "angle_count": angle_count,
        "median_x_count": median_x_count_by_angle(subset, args.angle_column, args.x_column),
        "center_x_zero_mm": summary.get("center_x_zero_mm"),
        "center_angle_deg": summary.get("center_angle_deg"),
        "center_axis_s_mm": summary.get("center_axis_s_mm"),
        "center_outward_mm": summary.get("center_outward_mm"),
        "template_score": summary.get("template_score"),
        "combined_score": summary.get("combined_score"),
        "downsampled_csv": str(downsampled_csv),
        "combined_html": str(combined_html),
    }


def add_errors(results: list[dict[str, object]], pipe_radius_mm: float) -> dict[str, object]:
    reference = results[0]
    ref_x = reference.get("center_x_zero_mm")
    ref_angle = reference.get("center_angle_deg")
    for row in results:
        x = row.get("center_x_zero_mm")
        angle = row.get("center_angle_deg")
        dx = None
        if isinstance(x, (int, float)) and isinstance(ref_x, (int, float)):
            dx = float(x) - float(ref_x)
        da = circular_angle_error_deg(angle if isinstance(angle, (int, float)) else None, ref_angle if isinstance(ref_angle, (int, float)) else None)
        dc = math.radians(da) * pipe_radius_mm if da is not None else None
        surface_error = None
        if dx is not None and dc is not None:
            surface_error = math.hypot(dx, dc)
        row["error_x_zero_mm"] = dx
        row["error_angle_deg"] = da
        row["error_circum_mm"] = dc
        row["error_surface_mm"] = surface_error
    return reference


def main() -> int:
    parser = argparse.ArgumentParser(description="Downsample high-density scan data and compare dent detection accuracy.")
    parser.add_argument("--input", type=Path, required=True, help="High-density angle/wheel scan CSV.")
    parser.add_argument("--output-dir", type=Path, default=None, help="Directory for generated files.")
    parser.add_argument("--angle-steps", default="5,10,15", help="Comma-separated angle intervals in deg. First is reference.")
    parser.add_argument("--x-steps", default="1,2,5,10", help="Comma-separated axial intervals in mm. First is reference.")
    parser.add_argument("--angle-column", default="angle_deg")
    parser.add_argument("--x-column", default="target_delta_mm", help="Use target_delta_mm for synthetic regular spacing.")
    parser.add_argument("--base-radius-mm", type=float, default=120.0)
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--hole-diameter-mm", type=float, default=154.0)
    parser.add_argument("--edge-percentile", type=float, default=97.0)
    parser.add_argument("--axis-section-bin-mm", type=float, default=20.0)
    parser.add_argument("--axis-min-section-points", type=int, default=20)
    parser.add_argument("--min-rows", type=int, default=30)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    args.input = args.input if args.input.is_absolute() else script_dir / args.input
    args.output_dir = args.output_dir or script_dir / f"{args.input.stem}_resolution_study"
    args.output_dir = args.output_dir if args.output_dir.is_absolute() else script_dir / args.output_dir
    args.output_dir.mkdir(parents=True, exist_ok=True)

    angle_steps = parse_number_list(args.angle_steps, float)
    x_steps = parse_number_list(args.x_steps, float)
    fieldnames, rows = read_rows(args.input)
    if args.angle_column not in fieldnames:
        raise SystemExit(f"missing angle column: {args.angle_column}")
    if args.x_column not in fieldnames:
        raise SystemExit(f"missing x column: {args.x_column}")

    print(f"[INPUT] {args.input}")
    print(f"[OUTPUT] {args.output_dir}")
    print(f"[ROWS] {len(rows)}")

    results: list[dict[str, object]] = []
    for angle_step in angle_steps:
        for x_step in x_steps:
            print(f"\n[CONDITION] angle_step={angle_step:g} deg, x_step={x_step:g} mm")
            result = process_condition(args, script_dir, fieldnames, rows, angle_step, x_step)
            results.append(result)

    if not args.dry_run:
        reference = add_errors(results, args.pipe_radius_mm)
        result_csv = args.output_dir / f"{args.input.stem}_resolution_results.csv"
        result_html = args.output_dir / f"{args.input.stem}_resolution_results.html"
        write_result_csv(result_csv, results)
        write_result_html(result_html, results, f"Resolution study: {args.input.stem}", reference)
        print(f"\n[RESULT] {result_csv}")
        print(f"[RESULT] {result_html}")

        best = sorted(
            (r for r in results[1:] if isinstance(r.get("error_surface_mm"), (int, float))),
            key=lambda r: float(r["error_surface_mm"]),
        )
        if best:
            r = best[0]
            print(
                "[BEST NON-REFERENCE] "
                f"{r['condition']}: surface_error={fmt_float(r.get('error_surface_mm'), 3)} mm, "
                f"score={fmt_float(r.get('combined_score'), 3)}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
