"""
Detect circular/elliptical dent-like regions from filtered pipe scan data.

Use this after:
    python dent_filter_detect.py --input pipe_measure_240mm.csv ...

This script works on the unwrapped pipe map:
    x = DL50 axis distance [mm]
    s = pipe_radius_mm * angle_rad [mm]

It thresholds positive outward displacement, groups connected points, and fits
a weighted ellipse to each region. It also overlays the expected known hole
diameter, centered at the fitted region center, for visual comparison.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import deque
from pathlib import Path
from statistics import median
from typing import Optional


REGION_COLUMNS = [
    "region_id",
    "point_count",
    "center_axis_mm",
    "center_angle_deg",
    "center_circum_mm",
    "major_diameter_mm",
    "minor_diameter_mm",
    "ellipse_angle_deg",
    "axis_min_mm",
    "axis_max_mm",
    "angle_min_deg",
    "angle_max_deg",
    "circum_min_mm",
    "circum_max_mm",
    "max_outward_mm",
    "mean_outward_mm",
    "ellipse_area_mm2",
    "expected_diameter_mm",
    "diameter_error_mm",
    "aspect_ratio",
    "circle_score",
]


BOUNDARY_COLUMNS = [
    "region_id",
    "boundary_type",
    "boundary_index",
    "axis_mm",
    "angle_deg",
    "circum_mm",
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


def median_spacing(values: list[float], fallback: float) -> float:
    unique = sorted(set(round(v, 6) for v in values))
    if len(unique) < 2:
        return fallback
    diffs = [b - a for a, b in zip(unique, unique[1:]) if b > a]
    return median(diffs) if diffs else fallback


def median_axis_spacing_by_angle(points: list[dict[str, float]], fallback: float) -> float:
    spacings: list[float] = []
    angles = sorted(set(p["angle_deg"] for p in points))
    for angle in angles:
        axes = [p["axis_mm"] for p in points if p["angle_deg"] == angle]
        value = median_spacing(axes, 0.0)
        if value > 0:
            spacings.append(value)
    return median(spacings) if spacings else fallback


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((pct / 100.0) * (len(ordered) - 1))))
    return ordered[idx]


def load_points(input_path: Path, pipe_radius_mm: float, value_column: str) -> list[dict[str, float]]:
    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    points: list[dict[str, float]] = []
    for row in rows:
        axis = parse_float(row.get("axis_mm"))
        angle = parse_float(row.get("angle_deg"))
        outward = parse_float(row.get(value_column))
        if axis is None or angle is None or outward is None:
            continue
        circum = pipe_radius_mm * math.radians(angle)
        points.append(
            {
                "axis_mm": axis,
                "angle_deg": angle,
                "circum_mm": circum,
                "outward_mm": outward,
            }
        )
    points.sort(key=lambda p: (p["angle_deg"], p["axis_mm"]))
    return points


def choose_threshold(points: list[dict[str, float]], threshold_mm: Optional[float], percentile_pct: float) -> float:
    if threshold_mm is not None:
        return threshold_mm
    positives = [p["outward_mm"] for p in points if p["outward_mm"] > 0]
    if not positives:
        return 0.0
    return max(0.05, percentile(positives, percentile_pct))


def connected_regions(
    points: list[dict[str, float]],
    threshold_mm: float,
    max_axis_gap_mm: Optional[float],
    max_angle_gap_deg: Optional[float],
    min_points: int,
) -> list[list[int]]:
    selected = [i for i, p in enumerate(points) if p["outward_mm"] >= threshold_mm]
    if not selected:
        return []

    if max_axis_gap_mm is None:
        max_axis_gap_mm = median_axis_spacing_by_angle(points, 5.0) * 2.25
    if max_angle_gap_deg is None:
        max_angle_gap_deg = median_spacing([p["angle_deg"] for p in points], 15.0) * 1.25

    neighbors: dict[int, list[int]] = {i: [] for i in selected}
    selected_set = set(selected)
    selected_list = list(selected_set)
    for pos, a_idx in enumerate(selected_list):
        a = points[a_idx]
        for b_idx in selected_list[pos + 1 :]:
            b = points[b_idx]
            if (
                abs(a["axis_mm"] - b["axis_mm"]) <= max_axis_gap_mm
                and abs(a["angle_deg"] - b["angle_deg"]) <= max_angle_gap_deg
            ):
                neighbors[a_idx].append(b_idx)
                neighbors[b_idx].append(a_idx)

    visited: set[int] = set()
    regions: list[list[int]] = []
    for start in selected_list:
        if start in visited:
            continue
        queue: deque[int] = deque([start])
        visited.add(start)
        region: list[int] = []
        while queue:
            idx = queue.popleft()
            region.append(idx)
            for nxt in neighbors[idx]:
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        if len(region) >= min_points:
            regions.append(region)
    return regions


def weighted_ellipse(points: list[dict[str, float]], indices: list[int], threshold_mm: float) -> dict[str, float]:
    region_points = [points[i] for i in indices]
    weights = [max(1e-6, p["outward_mm"] - threshold_mm) for p in region_points]
    total_w = sum(weights)

    cx = sum(p["axis_mm"] * w for p, w in zip(region_points, weights)) / total_w
    cs = sum(p["circum_mm"] * w for p, w in zip(region_points, weights)) / total_w

    var_x = sum(w * (p["axis_mm"] - cx) ** 2 for p, w in zip(region_points, weights)) / total_w
    var_s = sum(w * (p["circum_mm"] - cs) ** 2 for p, w in zip(region_points, weights)) / total_w
    cov_xs = sum(w * (p["axis_mm"] - cx) * (p["circum_mm"] - cs) for p, w in zip(region_points, weights)) / total_w

    trace = var_x + var_s
    diff = var_x - var_s
    root = math.sqrt(diff * diff + 4.0 * cov_xs * cov_xs)
    lambda1 = max(0.0, (trace + root) / 2.0)
    lambda2 = max(0.0, (trace - root) / 2.0)

    # For a uniform filled ellipse, covariance eigenvalues are a^2/4 and b^2/4.
    semi_major = 2.0 * math.sqrt(lambda1)
    semi_minor = 2.0 * math.sqrt(lambda2)
    angle_rad = 0.5 * math.atan2(2.0 * cov_xs, diff)

    axis_values = [p["axis_mm"] for p in region_points]
    circum_values = [p["circum_mm"] for p in region_points]
    angle_values = [p["angle_deg"] for p in region_points]
    outward_values = [p["outward_mm"] for p in region_points]

    return {
        "center_axis_mm": cx,
        "center_circum_mm": cs,
        "major_diameter_mm": 2.0 * semi_major,
        "minor_diameter_mm": 2.0 * semi_minor,
        "ellipse_angle_deg": math.degrees(angle_rad),
        "axis_min_mm": min(axis_values),
        "axis_max_mm": max(axis_values),
        "circum_min_mm": min(circum_values),
        "circum_max_mm": max(circum_values),
        "angle_min_deg": min(angle_values),
        "angle_max_deg": max(angle_values),
        "max_outward_mm": max(outward_values),
        "mean_outward_mm": sum(outward_values) / len(outward_values),
        "ellipse_area_mm2": math.pi * semi_major * semi_minor,
    }


def boundary_points(
    region_id: int,
    boundary_type: str,
    center_axis: float,
    center_circum: float,
    semi_major: float,
    semi_minor: float,
    angle_deg: float,
    pipe_radius_mm: float,
) -> list[dict[str, float | str]]:
    out: list[dict[str, float | str]] = []
    phi = math.radians(angle_deg)
    cos_phi = math.cos(phi)
    sin_phi = math.sin(phi)
    for i in range(181):
        t = 2.0 * math.pi * i / 180.0
        u = semi_major * math.cos(t)
        v = semi_minor * math.sin(t)
        axis = center_axis + u * cos_phi - v * sin_phi
        circum = center_circum + u * sin_phi + v * cos_phi
        out.append(
            {
                "region_id": region_id,
                "boundary_type": boundary_type,
                "boundary_index": i,
                "axis_mm": axis,
                "angle_deg": math.degrees(circum / pipe_radius_mm),
                "circum_mm": circum,
            }
        )
    return out


def build_regions(
    points: list[dict[str, float]],
    raw_regions: list[list[int]],
    threshold_mm: float,
    expected_diameter_mm: float,
    pipe_radius_mm: float,
) -> tuple[list[dict[str, float]], list[dict[str, float | str]]]:
    summaries: list[dict[str, float]] = []
    boundaries: list[dict[str, float | str]] = []
    expected_radius = expected_diameter_mm / 2.0

    for region_id, indices in enumerate(raw_regions, start=1):
        fit = weighted_ellipse(points, indices, threshold_mm)
        major = fit["major_diameter_mm"]
        minor = fit["minor_diameter_mm"]
        mean_diameter = (major + minor) / 2.0
        aspect_ratio = minor / major if major > 0 else 0.0
        diameter_error = mean_diameter - expected_diameter_mm
        # The hole diameter is known, so diameter agreement matters more than
        # perfect circularity. The measured high-depth core can look elliptical
        # because of sampling angle pitch and thresholding.
        diameter_scale = max(expected_diameter_mm * 0.25, 1.0)
        circle_score = max(0.0, aspect_ratio) * math.exp(-abs(diameter_error) / diameter_scale)
        center_angle = math.degrees(fit["center_circum_mm"] / pipe_radius_mm)

        summary = {
            "region_id": float(region_id),
            "point_count": float(len(indices)),
            "center_axis_mm": fit["center_axis_mm"],
            "center_angle_deg": center_angle,
            "center_circum_mm": fit["center_circum_mm"],
            "major_diameter_mm": major,
            "minor_diameter_mm": minor,
            "ellipse_angle_deg": fit["ellipse_angle_deg"],
            "axis_min_mm": fit["axis_min_mm"],
            "axis_max_mm": fit["axis_max_mm"],
            "angle_min_deg": fit["angle_min_deg"],
            "angle_max_deg": fit["angle_max_deg"],
            "circum_min_mm": fit["circum_min_mm"],
            "circum_max_mm": fit["circum_max_mm"],
            "max_outward_mm": fit["max_outward_mm"],
            "mean_outward_mm": fit["mean_outward_mm"],
            "ellipse_area_mm2": fit["ellipse_area_mm2"],
            "expected_diameter_mm": expected_diameter_mm,
            "diameter_error_mm": diameter_error,
            "aspect_ratio": aspect_ratio,
            "circle_score": circle_score,
        }
        summaries.append(summary)

        boundaries.extend(
            boundary_points(
                region_id,
                "fitted_ellipse",
                fit["center_axis_mm"],
                fit["center_circum_mm"],
                major / 2.0,
                minor / 2.0,
                fit["ellipse_angle_deg"],
                pipe_radius_mm,
            )
        )
        boundaries.extend(
            boundary_points(
                region_id,
                "expected_circle",
                fit["center_axis_mm"],
                fit["center_circum_mm"],
                expected_radius,
                expected_radius,
                0.0,
                pipe_radius_mm,
            )
        )

    summaries.sort(key=lambda r: (r["circle_score"], r["max_outward_mm"], r["point_count"]), reverse=True)
    return summaries, boundaries


def write_regions(regions: list[dict[str, float]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=REGION_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in regions:
            writer.writerow({k: f"{row[k]:.6f}" for k in REGION_COLUMNS})


def write_boundaries(boundaries: list[dict[str, float | str]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=BOUNDARY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in boundaries:
            out = {}
            for key in BOUNDARY_COLUMNS:
                value = row[key]
                out[key] = value if isinstance(value, str) else f"{float(value):.6f}"
            writer.writerow(out)


def write_html(
    points: list[dict[str, float]],
    regions: list[dict[str, float]],
    boundaries: list[dict[str, float | str]],
    input_path: Path,
    output_path: Path,
    threshold_mm: float,
) -> None:
    boundary_traces = []
    for boundary_type, color, width in [
        ("expected_circle", "white", 5),
        ("fitted_ellipse", "black", 2),
    ]:
        region_ids = sorted({int(float(b["region_id"])) for b in boundaries if b["boundary_type"] == boundary_type})
        for region_id in region_ids:
            rows = [b for b in boundaries if b["boundary_type"] == boundary_type and int(float(b["region_id"])) == region_id]
            boundary_traces.append(
                {
                    "type": "scatter",
                    "mode": "lines",
                    "x": [b["axis_mm"] for b in rows],
                    "y": [b["angle_deg"] for b in rows],
                    "line": {"color": color, "width": width},
                    "name": f"{boundary_type} {region_id}",
                }
            )

    payload = {
        "input": input_path.name,
        "threshold": threshold_mm,
        "x": [p["axis_mm"] for p in points],
        "y": [p["angle_deg"] for p in points],
        "z": [p["outward_mm"] for p in points],
        "selectedX": [p["axis_mm"] for p in points if p["outward_mm"] >= threshold_mm],
        "selectedY": [p["angle_deg"] for p in points if p["outward_mm"] >= threshold_mm],
        "regions": regions,
        "boundaryTraces": boundary_traces,
    }

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Dent Ellipse Detection</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ padding: 12px 16px; border-bottom: 1px solid #ddd; }}
    h1 {{ margin: 0 0 6px; font-size: 18px; }}
    .meta {{ color: #555; font-size: 13px; }}
    #plot {{ height: 82vh; min-height: 600px; }}
  </style>
</head>
<body>
  <header>
    <h1>Dent Ellipse Detection</h1>
    <div class="meta" id="meta"></div>
  </header>
  <div id="plot"></div>
  <script>
    const data = {json.dumps(payload, ensure_ascii=False)};
    const best = data.regions[0];
    document.getElementById("meta").textContent = best
      ? `${{data.input}} | threshold=${{data.threshold.toFixed(4)}} mm | best center axis=${{best.center_axis_mm.toFixed(2)}} mm angle=${{best.center_angle_deg.toFixed(2)}} deg | score=${{best.circle_score.toFixed(3)}}`
      : `${{data.input}} | threshold=${{data.threshold.toFixed(4)}} mm | no ellipse region`;

    const traces = [
      {{
        type: "scatter",
        mode: "markers",
        x: data.x,
        y: data.y,
        marker: {{
          size: 7,
          color: data.z,
          colorscale: "Turbo",
          colorbar: {{ title: "outward + mm" }},
        }},
        hovertemplate: "axis=%{{x:.3f}} mm<br>angle=%{{y:.3f}} deg<br>outward=%{{marker.color:.6f}} mm<extra></extra>",
        name: "outward"
      }},
      {{
        type: "scatter",
        mode: "markers",
        x: data.selectedX,
        y: data.selectedY,
        marker: {{ symbol: "circle-open", size: 10, color: "white", line: {{ color: "black", width: 1.3 }} }},
        name: "thresholded points"
      }},
      ...data.boundaryTraces
    ];

    Plotly.newPlot("plot", traces, {{
      title: "Circle / ellipse-like dent region detection",
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
    parser = argparse.ArgumentParser(description="Detect circle/ellipse-like dent regions from filtered CSV.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--expected-diameter-mm", type=float, default=199.0)
    parser.add_argument("--value-column", default="radial_outward_mm")
    parser.add_argument("--threshold-mm", type=float, default=None)
    parser.add_argument("--threshold-percentile", type=float, default=75.0)
    parser.add_argument("--max-axis-gap-mm", type=float, default=None)
    parser.add_argument("--max-angle-gap-deg", type=float, default=None)
    parser.add_argument("--min-points", type=int, default=8)
    parser.add_argument("--output-regions", default=None)
    parser.add_argument("--output-boundaries", default=None)
    parser.add_argument("--output-html", default=None)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input CSV not found: {input_path}")
        return 1

    output_regions = (
        Path(args.output_regions)
        if args.output_regions
        else input_path.with_name(f"{input_path.stem}_ellipse_regions.csv")
    )
    output_boundaries = (
        Path(args.output_boundaries)
        if args.output_boundaries
        else input_path.with_name(f"{input_path.stem}_ellipse_boundaries.csv")
    )
    output_html = (
        Path(args.output_html)
        if args.output_html
        else input_path.with_name(f"{input_path.stem}_ellipse_detection.html")
    )

    points = load_points(input_path, args.pipe_radius_mm, args.value_column)
    if not points:
        print("No valid points found.")
        return 1

    threshold = choose_threshold(points, args.threshold_mm, args.threshold_percentile)
    raw_regions = connected_regions(
        points,
        threshold_mm=threshold,
        max_axis_gap_mm=args.max_axis_gap_mm,
        max_angle_gap_deg=args.max_angle_gap_deg,
        min_points=args.min_points,
    )
    regions, boundaries = build_regions(
        points,
        raw_regions,
        threshold_mm=threshold,
        expected_diameter_mm=args.expected_diameter_mm,
        pipe_radius_mm=args.pipe_radius_mm,
    )

    write_regions(regions, output_regions)
    write_boundaries(boundaries, output_boundaries)
    write_html(points, regions, boundaries, input_path, output_html, threshold)

    print(f"Input: {input_path.resolve()}")
    print(f"Threshold: {threshold:.6f} mm")
    print(f"Regions: {len(regions)}")
    if regions:
        best = regions[0]
        print(
            f"Best: center_axis={best['center_axis_mm']:.3f} mm, "
            f"center_angle={best['center_angle_deg']:.3f} deg, "
            f"major={best['major_diameter_mm']:.3f} mm, "
            f"minor={best['minor_diameter_mm']:.3f} mm, "
            f"score={best['circle_score']:.6f}"
        )
    print(f"Region CSV: {output_regions.resolve()}")
    print(f"Boundary CSV: {output_boundaries.resolve()}")
    print(f"HTML: {output_html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
