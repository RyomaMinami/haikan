"""
Fit a known-size circular dent/hole template to filtered pipe scan data.

Use this after dent_filter_detect.py.

The pipe surface is unwrapped into a local plane:
    x = DL50 axis distance [mm]
    s = pipe_radius_mm * angle_rad [mm]

A circular hole with known diameter should appear approximately as a circle
on this unwrapped map when the hole is small enough relative to the pipe.
For a 240 mm inner-diameter pipe and a 199 mm hole, the circumferential
angular span is about:
    199 / 120 rad = 95 deg

Output:
    1. Best-fit circle summary CSV
    2. Circle boundary points CSV
    3. Interactive HTML overlay
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import median
from typing import Optional


SUMMARY_COLUMNS = [
    "hole_diameter_mm",
    "hole_radius_mm",
    "pipe_radius_mm",
    "center_axis_mm",
    "center_angle_deg",
    "center_circum_mm",
    "score",
    "inside_mean_depth_mm",
    "outside_mean_depth_mm",
    "inside_points",
    "outside_points",
    "edge_points",
    "estimated_angle_span_deg",
]


BOUNDARY_COLUMNS = [
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


def load_points(input_path: Path, pipe_radius_mm: float) -> list[dict[str, float]]:
    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    points: list[dict[str, float]] = []
    for row in rows:
        axis = parse_float(row.get("axis_mm"))
        angle = parse_float(row.get("angle_deg"))
        depth = parse_float(row.get("dent_depth_mm"))
        if axis is None or angle is None or depth is None:
            continue
        circum = pipe_radius_mm * math.radians(angle)
        points.append(
            {
                "axis_mm": axis,
                "angle_deg": angle,
                "circum_mm": circum,
                "dent_depth_mm": depth,
            }
        )
    return points


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def median_spacing(values: list[float], fallback: float) -> float:
    unique = sorted(set(round(v, 6) for v in values))
    if len(unique) < 2:
        return fallback
    diffs = [b - a for a, b in zip(unique, unique[1:]) if b > a]
    return median(diffs) if diffs else fallback


def score_center(
    points: list[dict[str, float]],
    center_axis: float,
    center_circum: float,
    radius_mm: float,
    edge_band_mm: float,
    outside_margin_mm: float,
    min_inside_points: int,
) -> Optional[dict[str, float]]:
    inside: list[float] = []
    outside: list[float] = []
    edge: list[float] = []

    outside_r = radius_mm + outside_margin_mm
    for p in points:
        dx = p["axis_mm"] - center_axis
        ds = p["circum_mm"] - center_circum
        dist = math.hypot(dx, ds)
        depth = p["dent_depth_mm"]
        if dist <= radius_mm:
            inside.append(depth)
        elif dist <= outside_r:
            outside.append(depth)
        if abs(dist - radius_mm) <= edge_band_mm:
            edge.append(depth)

    if len(inside) < min_inside_points or not outside:
        return None

    inside_mean = mean(inside)
    outside_mean = mean(outside)
    edge_mean = mean(edge)

    # A good circular dent has high depth inside the known circle and lower
    # depth just outside it. Edge points help when the measured dent is strongest
    # near the boundary.
    contrast = inside_mean - outside_mean
    score = contrast + 0.25 * edge_mean
    return {
        "score": score,
        "inside_mean_depth_mm": inside_mean,
        "outside_mean_depth_mm": outside_mean,
        "inside_points": float(len(inside)),
        "outside_points": float(len(outside)),
        "edge_points": float(len(edge)),
    }


def search_best_center(
    points: list[dict[str, float]],
    hole_diameter_mm: float,
    grid_step_mm: Optional[float],
    edge_band_mm: float,
    outside_margin_mm: float,
    min_inside_points: int,
) -> dict[str, float]:
    radius_mm = hole_diameter_mm / 2.0
    axes = [p["axis_mm"] for p in points]
    circs = [p["circum_mm"] for p in points]
    depths = [p["dent_depth_mm"] for p in points]

    if grid_step_mm is None:
        axis_step = median_spacing(axes, 5.0)
        circum_step = median_spacing(circs, 5.0)
        grid_step_mm = max(2.5, min(axis_step, circum_step))

    # Prefer candidate-depth points as center seeds, then add a coarse grid.
    high_depth = sorted(set((p["axis_mm"], p["circum_mm"]) for p in points if p["dent_depth_mm"] >= max(depths) * 0.35))
    axis_min = min(axes) + radius_mm * 0.35
    axis_max = max(axes) - radius_mm * 0.35
    circum_min = min(circs) + radius_mm * 0.35
    circum_max = max(circs) - radius_mm * 0.35

    seeds: set[tuple[float, float]] = set(high_depth)
    x = axis_min
    while x <= axis_max:
        s = circum_min
        while s <= circum_max:
            seeds.add((x, s))
            s += grid_step_mm
        x += grid_step_mm

    best: Optional[dict[str, float]] = None
    for center_axis, center_circum in seeds:
        result = score_center(
            points,
            center_axis=center_axis,
            center_circum=center_circum,
            radius_mm=radius_mm,
            edge_band_mm=edge_band_mm,
            outside_margin_mm=outside_margin_mm,
            min_inside_points=min_inside_points,
        )
        if result is None:
            continue
        result.update(
            {
                "center_axis_mm": center_axis,
                "center_circum_mm": center_circum,
            }
        )
        if best is None or result["score"] > best["score"]:
            best = result

    if best is None:
        raise SystemExit("No valid circle fit found. Try lowering --min-inside-points or measuring more angles.")
    return best


def boundary_points(center_axis: float, center_circum: float, radius_mm: float, pipe_radius_mm: float) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for i in range(181):
        t = 2.0 * math.pi * i / 180.0
        axis = center_axis + radius_mm * math.cos(t)
        circum = center_circum + radius_mm * math.sin(t)
        angle = math.degrees(circum / pipe_radius_mm)
        out.append(
            {
                "boundary_index": float(i),
                "axis_mm": axis,
                "angle_deg": angle,
                "circum_mm": circum,
            }
        )
    return out


def write_summary(summary: dict[str, float], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerow({k: f"{summary[k]:.6f}" for k in SUMMARY_COLUMNS})


def write_boundary(points: list[dict[str, float]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=BOUNDARY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for p in points:
            writer.writerow({k: f"{p[k]:.6f}" for k in BOUNDARY_COLUMNS})


def write_html(
    points: list[dict[str, float]],
    boundary: list[dict[str, float]],
    summary: dict[str, float],
    input_path: Path,
    output_path: Path,
) -> None:
    payload = {
        "input": input_path.name,
        "x": [p["axis_mm"] for p in points],
        "y": [p["angle_deg"] for p in points],
        "z": [p["dent_depth_mm"] for p in points],
        "boundaryX": [p["axis_mm"] for p in boundary],
        "boundaryY": [p["angle_deg"] for p in boundary],
        "centerX": summary["center_axis_mm"],
        "centerY": summary["center_angle_deg"],
        "summary": summary,
    }

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Dent Circle Fit</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ padding: 12px 16px; border-bottom: 1px solid #ddd; }}
    h1 {{ margin: 0 0 6px; font-size: 18px; }}
    .meta {{ color: #555; font-size: 13px; }}
    #plot {{ height: 78vh; min-height: 560px; }}
  </style>
</head>
<body>
  <header>
    <h1>Dent Circle Fit</h1>
    <div class="meta" id="meta"></div>
  </header>
  <div id="plot"></div>
  <script>
    const data = {json.dumps(payload, ensure_ascii=False)};
    const s = data.summary;
    document.getElementById("meta").textContent =
      `${{data.input}} | diameter=${{s.hole_diameter_mm.toFixed(1)}} mm | center axis=${{s.center_axis_mm.toFixed(2)}} mm | center angle=${{s.center_angle_deg.toFixed(2)}} deg | score=${{s.score.toFixed(4)}}`;

    Plotly.newPlot("plot", [
      {{
        type: "scatter",
        mode: "markers",
        x: data.x,
        y: data.y,
        marker: {{
          size: 8,
          color: data.z,
          colorscale: "Turbo",
          colorbar: {{ title: "dent depth mm" }},
        }},
        hovertemplate: "axis=%{{x:.3f}} mm<br>angle=%{{y:.3f}} deg<br>depth=%{{marker.color:.6f}} mm<extra></extra>",
        name: "depth"
      }},
      {{
        type: "scatter",
        mode: "lines",
        x: data.boundaryX,
        y: data.boundaryY,
        line: {{ color: "white", width: 4 }},
        name: "199 mm circle"
      }},
      {{
        type: "scatter",
        mode: "lines",
        x: data.boundaryX,
        y: data.boundaryY,
        line: {{ color: "black", width: 1.5 }},
        name: "circle edge"
      }},
      {{
        type: "scatter",
        mode: "markers",
        x: [data.centerX],
        y: [data.centerY],
        marker: {{ color: "red", size: 12, symbol: "x" }},
        name: "center"
      }}
    ], {{
      title: "Known-size circle fit on unwrapped pipe map",
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
    parser = argparse.ArgumentParser(description="Fit known-size circular dent to filtered pipe scan data.")
    parser.add_argument("--input", required=True, help="Filtered CSV from dent_filter_detect.py.")
    parser.add_argument("--hole-diameter-mm", type=float, default=199.0)
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--grid-step-mm", type=float, default=None)
    parser.add_argument("--edge-band-mm", type=float, default=12.0)
    parser.add_argument("--outside-margin-mm", type=float, default=35.0)
    parser.add_argument("--min-inside-points", type=int, default=5)
    parser.add_argument("--output-summary", default=None)
    parser.add_argument("--output-boundary", default=None)
    parser.add_argument("--output-html", default=None)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input CSV not found: {input_path}")
        return 1

    output_summary = (
        Path(args.output_summary)
        if args.output_summary
        else input_path.with_name(f"{input_path.stem}_circle_fit.csv")
    )
    output_boundary = (
        Path(args.output_boundary)
        if args.output_boundary
        else input_path.with_name(f"{input_path.stem}_circle_boundary.csv")
    )
    output_html = (
        Path(args.output_html)
        if args.output_html
        else input_path.with_name(f"{input_path.stem}_circle_fit.html")
    )

    points = load_points(input_path, args.pipe_radius_mm)
    if not points:
        print("No valid filtered points found.")
        return 1

    radius = args.hole_diameter_mm / 2.0
    best = search_best_center(
        points,
        hole_diameter_mm=args.hole_diameter_mm,
        grid_step_mm=args.grid_step_mm,
        edge_band_mm=args.edge_band_mm,
        outside_margin_mm=args.outside_margin_mm,
        min_inside_points=args.min_inside_points,
    )
    center_angle = math.degrees(best["center_circum_mm"] / args.pipe_radius_mm)
    summary = {
        "hole_diameter_mm": args.hole_diameter_mm,
        "hole_radius_mm": radius,
        "pipe_radius_mm": args.pipe_radius_mm,
        "center_axis_mm": best["center_axis_mm"],
        "center_angle_deg": center_angle,
        "center_circum_mm": best["center_circum_mm"],
        "score": best["score"],
        "inside_mean_depth_mm": best["inside_mean_depth_mm"],
        "outside_mean_depth_mm": best["outside_mean_depth_mm"],
        "inside_points": best["inside_points"],
        "outside_points": best["outside_points"],
        "edge_points": best["edge_points"],
        "estimated_angle_span_deg": math.degrees(args.hole_diameter_mm / args.pipe_radius_mm),
    }
    boundary = boundary_points(best["center_axis_mm"], best["center_circum_mm"], radius, args.pipe_radius_mm)

    write_summary(summary, output_summary)
    write_boundary(boundary, output_boundary)
    write_html(points, boundary, summary, input_path, output_html)

    print(f"Input: {input_path.resolve()}")
    print(f"Hole diameter: {args.hole_diameter_mm:.3f} mm")
    print(f"Estimated angular span: {summary['estimated_angle_span_deg']:.3f} deg")
    print(f"Center axis: {summary['center_axis_mm']:.3f} mm")
    print(f"Center angle: {summary['center_angle_deg']:.3f} deg")
    print(f"Score: {summary['score']:.6f}")
    print(f"Summary CSV: {output_summary.resolve()}")
    print(f"Boundary CSV: {output_boundary.resolve()}")
    print(f"HTML: {output_html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
