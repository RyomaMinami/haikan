"""
Filter LK-G pipe scan data and detect dent-like candidate regions.

Input:
    Angle-step CSV from angle_step_trigger_logger.py.

Output:
    1. Filtered per-point CSV
    2. Candidate-region CSV
    3. Interactive HTML showing filtered depth and candidate points

Detection model:
    - Sort samples by angle and DL50 axis.
    - Apply a small median filter to suppress spikes.
    - Apply a small moving average to smooth noise.
    - Estimate a baseline with a larger moving average, a per-angle minimum,
      or a global minimum.
    - Dent depth/outward displacement = filtered - baseline, or baseline - filtered.
    - Points above a robust threshold are grouped into connected candidates.

Coordinate convention:
    dent_depth_mm and radial_outward_mm are positive in the physical outward
    radial direction. In the current LK-G85A setup, the dent area appears as a
    stronger negative LK-G value, so the default --dent-direction decrease maps
    that negative LK-G change to positive outward displacement.

If dents appear reversed, change --dent-direction from decrease to increase.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import OrderedDict, deque
from pathlib import Path
from statistics import median
from typing import Iterable, Optional


PREFERRED_LK_COLUMNS = [
    "lk_out1_corrected_mm",
    "lk_out1_mm",
    "lk_out2_corrected_mm",
    "lk_out2_mm",
]


FILTERED_COLUMNS = [
    "pc_time",
    "angle_group",
    "angle_deg",
    "axis_mm",
    "target_delta_mm",
    "dl50_delta_mm",
    "lk_raw_mm",
    "lk_median_mm",
    "lk_filtered_mm",
    "lk_baseline_mm",
    "dent_depth_mm",
    "radial_outward_mm",
    "is_candidate_point",
    "candidate_id",
]


CANDIDATE_COLUMNS = [
    "candidate_id",
    "point_count",
    "angle_min_deg",
    "angle_max_deg",
    "angle_center_deg",
    "axis_min_mm",
    "axis_max_mm",
    "axis_center_mm",
    "max_depth_mm",
    "mean_depth_mm",
    "center_angle_at_max_deg",
    "center_axis_at_max_mm",
    "approx_width_axis_mm",
    "approx_width_circum_mm",
]


def parse_float(value: object) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def pick_column(fieldnames: Iterable[str], requested: str | None, candidates: list[str]) -> str:
    fields = set(fieldnames)
    if requested:
        if requested not in fields:
            raise SystemExit(f"Column not found: {requested}")
        return requested
    for name in candidates:
        if name in fields:
            return name
    raise SystemExit(f"No usable column found. Tried: {', '.join(candidates)}")


def odd_window(value: int, minimum: int = 1) -> int:
    value = max(minimum, int(value))
    if value % 2 == 0:
        value += 1
    return value


def median_filter(values: list[float], window: int) -> list[float]:
    window = odd_window(window)
    half = window // 2
    out: list[float] = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        out.append(median(values[lo:hi]))
    return out


def moving_average(values: list[float], window: int) -> list[float]:
    window = odd_window(window)
    half = window // 2
    out: list[float] = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        segment = values[lo:hi]
        out.append(sum(segment) / len(segment))
    return out


def robust_sigma(values: list[float]) -> float:
    if not values:
        return 0.0
    med = median(values)
    deviations = [abs(v - med) for v in values]
    mad = median(deviations)
    return 1.4826 * mad


def median_spacing(values: list[float], fallback: float) -> float:
    unique = sorted(set(values))
    if len(unique) < 2:
        return fallback
    diffs = [b - a for a, b in zip(unique, unique[1:]) if b > a]
    if not diffs:
        return fallback
    return median(diffs)


def load_rows(input_path: Path, lk_column: str | None, x_column: str | None) -> tuple[list[dict[str, object]], str, str]:
    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        chosen_lk = pick_column(fieldnames, lk_column, PREFERRED_LK_COLUMNS)
        chosen_x = pick_column(fieldnames, x_column, ["dl50_delta_mm", "target_delta_mm", "dl50_hi_mm"])
        raw_rows = list(reader)

    rows: list[dict[str, object]] = []
    seen: set[tuple[int, int]] = set()
    for row in raw_rows:
        angle = parse_float(row.get("angle_deg"))
        axis = parse_float(row.get(chosen_x))
        lk = parse_float(row.get(chosen_lk))
        if angle is None or axis is None or lk is None:
            continue

        key = (round(angle * 1000), round(axis * 1000))
        if key in seen:
            continue
        seen.add(key)

        rows.append(
            {
                "pc_time": row.get("pc_time", ""),
                "angle_group": row.get("angle_group", ""),
                "angle_deg": angle,
                "axis_mm": axis,
                "target_delta_mm": row.get("target_delta_mm", ""),
                "dl50_delta_mm": row.get("dl50_delta_mm", ""),
                "lk_raw_mm": lk,
            }
        )

    rows.sort(key=lambda r: (float(r["angle_deg"]), float(r["axis_mm"])))
    return rows, chosen_lk, chosen_x


def filter_rows(
    rows: list[dict[str, object]],
    median_window: int,
    smooth_window: int,
    baseline_window: int,
    baseline_mode: str,
    dent_direction: str,
) -> list[dict[str, object]]:
    groups: OrderedDict[float, list[dict[str, object]]] = OrderedDict()
    for row in rows:
        groups.setdefault(float(row["angle_deg"]), []).append(row)

    filtered: list[dict[str, object]] = []
    sign = -1.0 if dent_direction == "decrease" else 1.0
    global_values = [float(r["lk_raw_mm"]) for r in rows]
    global_min = min(global_values) if global_values else 0.0
    global_max = max(global_values) if global_values else 0.0

    for _, group_rows in groups.items():
        values = [float(r["lk_raw_mm"]) for r in group_rows]
        med = median_filter(values, median_window)
        smoothed = moving_average(med, smooth_window)
        if baseline_mode == "moving":
            baseline = moving_average(smoothed, baseline_window)
        elif baseline_mode == "min-per-angle":
            baseline = [min(smoothed)] * len(smoothed)
        elif baseline_mode == "global-min":
            baseline = [global_min] * len(smoothed)
        elif baseline_mode == "max-per-angle":
            baseline = [max(smoothed)] * len(smoothed)
        elif baseline_mode == "global-max":
            baseline = [global_max] * len(smoothed)
        else:
            raise ValueError(f"Unknown baseline mode: {baseline_mode}")

        for row, med_v, smooth_v, base_v in zip(group_rows, med, smoothed, baseline):
            depth = sign * (smooth_v - base_v)
            new_row = dict(row)
            new_row.update(
                {
                    "lk_median_mm": med_v,
                    "lk_filtered_mm": smooth_v,
                    "lk_baseline_mm": base_v,
                    "dent_depth_mm": depth,
                    "radial_outward_mm": depth,
                    "is_candidate_point": 0,
                    "candidate_id": "",
                }
            )
            filtered.append(new_row)

    return filtered


def choose_threshold(filtered: list[dict[str, object]], threshold_mm: Optional[float], sigma: float) -> float:
    if threshold_mm is not None:
        return threshold_mm
    depths = [float(r["dent_depth_mm"]) for r in filtered]
    positive = [max(0.0, d) for d in depths]
    noise = robust_sigma(positive)
    if noise <= 0:
        noise = robust_sigma(depths)
    return max(0.05, sigma * noise)


def grid_indices(rows: list[dict[str, object]]) -> tuple[list[float], list[float], dict[tuple[int, int], int]]:
    angles = sorted(OrderedDict((float(r["angle_deg"]), None) for r in rows).keys())
    axes = sorted(OrderedDict((float(r["axis_mm"]), None) for r in rows).keys())
    angle_index = {round(v * 1000): i for i, v in enumerate(angles)}
    axis_index = {round(v * 1000): i for i, v in enumerate(axes)}
    mapping: dict[tuple[int, int], int] = {}
    for idx, row in enumerate(rows):
        ai = angle_index[round(float(row["angle_deg"]) * 1000)]
        xi = axis_index[round(float(row["axis_mm"]) * 1000)]
        mapping[(ai, xi)] = idx
    return angles, axes, mapping


def detect_candidates(
    rows: list[dict[str, object]],
    threshold_mm: float,
    min_points: int,
    min_depth_mm: float,
    pipe_radius_mm: float,
    max_axis_gap_mm: Optional[float],
    max_angle_gap_deg: Optional[float],
    edge_margin_mm: float,
) -> list[dict[str, object]]:
    groups: OrderedDict[float, list[int]] = OrderedDict()
    for idx, row in enumerate(rows):
        groups.setdefault(float(row["angle_deg"]), []).append(idx)

    usable_rows: list[tuple[int, dict[str, object]]] = []
    for angle_indices in groups.values():
        angle_rows = [rows[idx] for idx in angle_indices]
        axis_values = [float(r["axis_mm"]) for r in angle_rows]
        axis_min = min(axis_values)
        axis_max = max(axis_values)
        for idx in angle_indices:
            row = rows[idx]
            axis = float(row["axis_mm"])
            if edge_margin_mm > 0 and (axis < axis_min + edge_margin_mm or axis > axis_max - edge_margin_mm):
                continue
            if float(row["dent_depth_mm"]) >= threshold_mm:
                usable_rows.append((idx, row))

    angle_values = [float(r["angle_deg"]) for _, r in usable_rows]
    axis_values = [float(r["axis_mm"]) for _, r in usable_rows]
    if max_axis_gap_mm is None:
        max_axis_gap_mm = median_spacing([float(r["axis_mm"]) for r in rows], 5.0) * 1.75
    if max_angle_gap_deg is None:
        max_angle_gap_deg = median_spacing([float(r["angle_deg"]) for r in rows], 360.0) * 1.75
    if len(set(angle_values)) <= 1:
        max_angle_gap_deg = 0.001
    if len(set(axis_values)) <= 1:
        max_axis_gap_mm = 0.001

    candidate_indices = {idx for idx, _ in usable_rows}
    neighbors: dict[int, list[int]] = {idx: [] for idx in candidate_indices}
    usable_list = list(candidate_indices)
    for pos, a_idx in enumerate(usable_list):
        a = rows[a_idx]
        for b_idx in usable_list[pos + 1 :]:
            b = rows[b_idx]
            if (
                abs(float(a["axis_mm"]) - float(b["axis_mm"])) <= max_axis_gap_mm
                and abs(float(a["angle_deg"]) - float(b["angle_deg"])) <= max_angle_gap_deg
            ):
                neighbors[a_idx].append(b_idx)
                neighbors[b_idx].append(a_idx)

    visited: set[int] = set()
    candidates: list[dict[str, object]] = []
    candidate_id = 1

    for start in sorted(candidate_indices):
        if start in visited:
            continue
        queue: deque[int] = deque([start])
        visited.add(start)
        indices: list[int] = []

        while queue:
            idx = queue.popleft()
            indices.append(idx)
            for nxt in neighbors[idx]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)

        point_rows = [rows[idx] for idx in indices]
        if len(point_rows) < min_points:
            continue

        depths = [float(r["dent_depth_mm"]) for r in point_rows]
        max_depth = max(depths)
        if max_depth < min_depth_mm:
            continue

        max_row = point_rows[depths.index(max_depth)]
        angle_values = [float(r["angle_deg"]) for r in point_rows]
        axis_values = [float(r["axis_mm"]) for r in point_rows]
        angle_min = min(angle_values)
        angle_max = max(angle_values)
        axis_min = min(axis_values)
        axis_max = max(axis_values)
        angle_width_rad = math.radians(max(0.0, angle_max - angle_min))

        for row in point_rows:
            row["is_candidate_point"] = 1
            row["candidate_id"] = candidate_id

        candidates.append(
            {
                "candidate_id": candidate_id,
                "point_count": len(point_rows),
                "angle_min_deg": angle_min,
                "angle_max_deg": angle_max,
                "angle_center_deg": (angle_min + angle_max) / 2.0,
                "axis_min_mm": axis_min,
                "axis_max_mm": axis_max,
                "axis_center_mm": (axis_min + axis_max) / 2.0,
                "max_depth_mm": max_depth,
                "mean_depth_mm": sum(depths) / len(depths),
                "center_angle_at_max_deg": float(max_row["angle_deg"]),
                "center_axis_at_max_mm": float(max_row["axis_mm"]),
                "approx_width_axis_mm": axis_max - axis_min,
                "approx_width_circum_mm": pipe_radius_mm * angle_width_rad,
            }
        )
        candidate_id += 1

    candidates.sort(key=lambda c: float(c["max_depth_mm"]), reverse=True)
    return candidates


def fmt(value: object) -> object:
    if isinstance(value, float):
        return f"{value:.6f}"
    return value


def write_filtered(rows: list[dict[str, object]], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FILTERED_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: fmt(row.get(k, "")) for k in FILTERED_COLUMNS})


def write_candidates(candidates: list[dict[str, object]], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CANDIDATE_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in candidates:
            writer.writerow({k: fmt(row.get(k, "")) for k in CANDIDATE_COLUMNS})


def heatmap(rows: list[dict[str, object]], value_column: str) -> dict[str, object]:
    angles = sorted(OrderedDict((float(r["angle_deg"]), None) for r in rows).keys())
    axes = sorted(OrderedDict((float(r["axis_mm"]), None) for r in rows).keys())
    by_key = {
        (round(float(r["angle_deg"]) * 1000), round(float(r["axis_mm"]) * 1000)): float(r[value_column])
        for r in rows
    }
    z: list[list[Optional[float]]] = []
    for angle in angles:
        line: list[Optional[float]] = []
        for axis in axes:
            line.append(by_key.get((round(angle * 1000), round(axis * 1000))))
        z.append(line)
    return {"angles": angles, "axes": axes, "z": z}


def write_html(
    rows: list[dict[str, object]],
    candidates: list[dict[str, object]],
    output_path: Path,
    input_path: Path,
    chosen_lk: str,
    chosen_x: str,
    threshold_mm: float,
) -> None:
    depth_map = heatmap(rows, "radial_outward_mm")
    filtered_map = heatmap(rows, "lk_filtered_mm")
    candidate_x = [float(r["axis_mm"]) for r in rows if int(r["is_candidate_point"]) == 1]
    candidate_y = [float(r["angle_deg"]) for r in rows if int(r["is_candidate_point"]) == 1]
    candidate_text = [
        f"candidate={r['candidate_id']}<br>axis={float(r['axis_mm']):.3f} mm<br>"
        f"angle={float(r['angle_deg']):.3f} deg<br>outward={float(r['radial_outward_mm']):.6f} mm"
        for r in rows
        if int(r["is_candidate_point"]) == 1
    ]

    payload = {
        "input": input_path.name,
        "chosenLk": chosen_lk,
        "chosenX": chosen_x,
        "threshold": threshold_mm,
        "depth": depth_map,
        "filtered": filtered_map,
        "candidateX": candidate_x,
        "candidateY": candidate_y,
        "candidateText": candidate_text,
        "candidateCount": len(candidates),
    }

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Dent Filter and Detection</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ padding: 12px 16px; border-bottom: 1px solid #ddd; }}
    h1 {{ margin: 0 0 6px; font-size: 18px; }}
    .meta {{ color: #555; font-size: 13px; }}
    .plot {{ height: 45vh; min-height: 360px; padding: 10px; }}
  </style>
</head>
<body>
  <header>
    <h1>Dent Filter and Detection</h1>
    <div class="meta" id="meta"></div>
  </header>
  <div id="depth" class="plot"></div>
  <div id="filtered" class="plot"></div>
  <script>
    const data = {json.dumps(payload, ensure_ascii=False)};
    document.getElementById("meta").textContent =
      `${{data.input}} | LK=${{data.chosenLk}} | x=${{data.chosenX}} | outward + threshold=${{data.threshold.toFixed(6)}} mm | candidates=${{data.candidateCount}}`;

    Plotly.newPlot("depth", [
      {{
        type: "heatmap",
        x: data.depth.axes,
        y: data.depth.angles,
        z: data.depth.z,
        colorscale: "Turbo",
        colorbar: {{ title: "outward + mm" }},
        hovertemplate: "axis=%{{x:.3f}} mm<br>angle=%{{y:.3f}} deg<br>outward +%{{z:.6f}} mm<extra></extra>"
      }},
      {{
        type: "scatter",
        mode: "markers",
        x: data.candidateX,
        y: data.candidateY,
        text: data.candidateText,
        hoverinfo: "text",
        marker: {{ symbol: "circle-open", color: "white", size: 10, line: {{ color: "black", width: 1.5 }} }},
        name: "candidate"
      }}
    ], {{
      title: "Physical outward dent map (+ is pipe-radius outward)",
      xaxis: {{ title: "pipe axis [mm]" }},
      yaxis: {{ title: "servo angle [deg]" }},
      margin: {{ l: 70, r: 20, t: 42, b: 60 }}
    }}, {{ responsive: true }});

    Plotly.newPlot("filtered", [{{
      type: "heatmap",
      x: data.filtered.axes,
      y: data.filtered.angles,
      z: data.filtered.z,
      colorscale: "Viridis",
      colorbar: {{ title: "filtered LK mm" }},
      hovertemplate: "axis=%{{x:.3f}} mm<br>angle=%{{y:.3f}} deg<br>LK=%{{z:.6f}} mm<extra></extra>"
    }}], {{
      title: "Filtered LK-G map (sensor sign, before physical sign conversion)",
      xaxis: {{ title: "pipe axis [mm]" }},
      yaxis: {{ title: "servo angle [deg]" }},
      margin: {{ l: 70, r: 20, t: 42, b: 60 }}
    }}, {{ responsive: true }});
  </script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter pipe scan data and detect dent candidates.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-filtered", default=None)
    parser.add_argument("--output-candidates", default=None)
    parser.add_argument("--output-html", default=None)
    parser.add_argument("--lk-column", default=None)
    parser.add_argument("--x-column", default=None)
    parser.add_argument("--median-window", type=int, default=3)
    parser.add_argument("--smooth-window", type=int, default=5)
    parser.add_argument("--baseline-window", type=int, default=31)
    parser.add_argument(
        "--baseline-mode",
        choices=["moving", "min-per-angle", "global-min", "max-per-angle", "global-max"],
        default="moving",
        help=(
            "Baseline calculation. Use min-per-angle or global-min when you "
            "want the lowest measured surface to become zero."
        ),
    )
    parser.add_argument("--dent-direction", choices=["decrease", "increase"], default="decrease")
    parser.add_argument("--threshold-mm", type=float, default=None)
    parser.add_argument("--threshold-sigma", type=float, default=4.0)
    parser.add_argument("--min-points", type=int, default=3)
    parser.add_argument("--min-depth-mm", type=float, default=0.1)
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--max-axis-gap-mm", type=float, default=None)
    parser.add_argument("--max-angle-gap-deg", type=float, default=None)
    parser.add_argument(
        "--edge-margin-mm",
        type=float,
        default=0.0,
        help="Ignore candidate points this close to each angle scan edge.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input CSV not found: {input_path}")
        return 1

    output_filtered = (
        Path(args.output_filtered)
        if args.output_filtered
        else input_path.with_name(f"{input_path.stem}_filtered.csv")
    )
    output_candidates = (
        Path(args.output_candidates)
        if args.output_candidates
        else input_path.with_name(f"{input_path.stem}_dent_candidates.csv")
    )
    output_html = (
        Path(args.output_html)
        if args.output_html
        else input_path.with_name(f"{input_path.stem}_dent_detection.html")
    )

    rows, chosen_lk, chosen_x = load_rows(input_path, args.lk_column, args.x_column)
    if not rows:
        print("No valid rows found.")
        return 1

    filtered = filter_rows(
        rows,
        median_window=args.median_window,
        smooth_window=args.smooth_window,
        baseline_window=args.baseline_window,
        baseline_mode=args.baseline_mode,
        dent_direction=args.dent_direction,
    )
    threshold = choose_threshold(filtered, args.threshold_mm, args.threshold_sigma)
    candidates = detect_candidates(
        filtered,
        threshold_mm=threshold,
        min_points=args.min_points,
        min_depth_mm=args.min_depth_mm,
        pipe_radius_mm=args.pipe_radius_mm,
        max_axis_gap_mm=args.max_axis_gap_mm,
        max_angle_gap_deg=args.max_angle_gap_deg,
        edge_margin_mm=args.edge_margin_mm,
    )

    write_filtered(filtered, output_filtered)
    write_candidates(candidates, output_candidates)
    write_html(filtered, candidates, output_html, input_path, chosen_lk, chosen_x, threshold)

    print(f"Input: {input_path.resolve()}")
    print(f"LK column: {chosen_lk}")
    print(f"X column: {chosen_x}")
    print(f"Dent direction: {args.dent_direction}")
    print(f"Baseline mode: {args.baseline_mode}")
    print(f"Threshold: {threshold:.6f} mm")
    print(f"Rows: {len(filtered)}")
    print(f"Candidates: {len(candidates)}")
    print(f"Filtered CSV: {output_filtered.resolve()}")
    print(f"Candidate CSV: {output_candidates.resolve()}")
    print(f"HTML: {output_html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
