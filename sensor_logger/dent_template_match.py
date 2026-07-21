"""
Detect a known circular dent by matching an expected sensor-view template.

Use this after dent_filter_detect.py:
    python dent_filter_detect.py --input pipe_measure_240mm.csv ...

Concept:
    The pipe surface is unwrapped into a local plane:
        x = DL50 axis distance [mm]
        s = pipe_radius_mm * angle_rad [mm]

    For each possible center, build an expected "what the sensor should see"
    template for a circular dent/hole of known diameter. Compare it with the
    measured outward displacement and choose the center with the highest
    normalized correlation score.

Template modes:
    filled  : inside the known circle is high, outside is low.
    edge    : boundary ring is high, elsewhere low.
    hybrid  : weighted mix of filled and edge. This is the default because
              real LK-G scans often show both a broad raised/depressed area
              and a stronger boundary response.

Outputs:
    1. Match summary CSV
    2. Point CSV with recognized in-circle/edge flags
    3. Boundary CSV
    4. HTML with before/after 3D maps and a 2D unwrapped overlay
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Optional


SUMMARY_COLUMNS = [
    "hole_diameter_mm",
    "hole_radius_mm",
    "pipe_radius_mm",
    "template_mode",
    "center_axis_mm",
    "center_angle_deg",
    "center_circum_mm",
    "match_score",
    "inside_mean_outward_mm",
    "outside_mean_outward_mm",
    "edge_mean_outward_mm",
    "inside_points",
    "outside_points",
    "edge_points",
    "estimated_angle_span_deg",
]


POINT_COLUMNS = [
    "axis_mm",
    "angle_deg",
    "circum_mm",
    "outward_mm",
    "radius_mm",
    "x_mm",
    "y_mm",
    "z_mm",
    "template_value",
    "recognized_inside",
    "recognized_edge",
]


BOUNDARY_COLUMNS = [
    "boundary_index",
    "axis_mm",
    "angle_deg",
    "circum_mm",
    "x_mm",
    "y_mm",
    "z_mm",
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
    for angle in sorted(set(p["angle_deg"] for p in points)):
        axes = [p["axis_mm"] for p in points if p["angle_deg"] == angle]
        spacing = median_spacing(axes, 0.0)
        if spacing > 0:
            spacings.append(spacing)
    return median(spacings) if spacings else fallback


def load_points(input_path: Path, value_column: str, pipe_radius_mm: float, base_radius_mm: float) -> list[dict[str, float]]:
    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    points: list[dict[str, float]] = []
    seen: set[tuple[int, int]] = set()
    for row in rows:
        axis = parse_float(row.get("axis_mm"))
        angle = parse_float(row.get("angle_deg"))
        outward = parse_float(row.get(value_column))
        if axis is None or angle is None or outward is None:
            continue

        key = (round(axis * 1000), round(angle * 1000))
        if key in seen:
            continue
        seen.add(key)

        circum = pipe_radius_mm * math.radians(angle)
        radius = base_radius_mm + outward
        theta = math.radians(angle)
        points.append(
            {
                "axis_mm": axis,
                "angle_deg": angle,
                "circum_mm": circum,
                "outward_mm": outward,
                "radius_mm": radius,
                "x_mm": axis,
                "y_mm": radius * math.cos(theta),
                "z_mm": radius * math.sin(theta),
            }
        )

    points.sort(key=lambda p: (p["angle_deg"], p["axis_mm"]))
    return points


def pick_existing_column(fieldnames: list[str], requested: Optional[str], candidates: list[str]) -> str:
    fields = set(fieldnames)
    if requested:
        if requested not in fields:
            raise SystemExit(f"Column not found: {requested}")
        return requested
    for name in candidates:
        if name in fields:
            return name
    raise SystemExit(f"No usable column found. Tried: {', '.join(candidates)}")


def reference_value(values: list[float], mode: str, explicit: Optional[float]) -> float:
    if explicit is not None:
        return explicit
    if not values:
        raise SystemExit("No valid surface LK values found.")
    if mode == "first":
        return values[0]
    if mode == "mean":
        return mean(values)
    if mode == "median":
        return median(values)
    raise SystemExit(f"Unknown reference mode: {mode}")


def load_surface_points(
    input_path: Path,
    pipe_radius_mm: float,
    base_radius_mm: float,
    lk_column: Optional[str],
    x_column: Optional[str],
    lk_reference_mode: str,
    lk_reference_mm: Optional[float],
    invert_lk: bool,
) -> list[dict[str, float]]:
    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        chosen_lk = pick_existing_column(
            fieldnames,
            lk_column,
            ["lk_out1_corrected_mm", "lk_out1_mm", "lk_out2_corrected_mm", "lk_out2_mm", "radial_outward_mm"],
        )
        chosen_x = pick_existing_column(fieldnames, x_column, ["axis_mm", "dl50_delta_mm", "target_delta_mm", "dl50_hi_mm"])
        rows = list(reader)

    raw: list[tuple[float, float, float]] = []
    for row in rows:
        axis = parse_float(row.get(chosen_x))
        angle = parse_float(row.get("angle_deg"))
        value = parse_float(row.get(chosen_lk))
        if axis is None or angle is None or value is None:
            continue
        raw.append((axis, angle, value))

    if chosen_lk == "radial_outward_mm":
        ref = 0.0
        sign = 1.0
    else:
        ref = reference_value([v for _, _, v in raw], lk_reference_mode, lk_reference_mm)
        sign = -1.0 if invert_lk else 1.0

    points: list[dict[str, float]] = []
    seen: set[tuple[int, int]] = set()
    for axis, angle, value in raw:
        key = (round(axis * 1000), round(angle * 1000))
        if key in seen:
            continue
        seen.add(key)

        outward = sign * (value - ref)
        radius = base_radius_mm + outward
        theta = math.radians(angle)
        circum = pipe_radius_mm * theta
        points.append(
            {
                "axis_mm": axis,
                "angle_deg": angle,
                "circum_mm": circum,
                "outward_mm": outward,
                "radius_mm": radius,
                "x_mm": axis,
                "y_mm": radius * math.cos(theta),
                "z_mm": radius * math.sin(theta),
            }
        )

    points.sort(key=lambda p: (p["angle_deg"], p["axis_mm"]))
    return points


def template_value(dist: float, radius: float, edge_band: float, mode: str) -> float:
    inside = 1.0 if dist <= radius else -0.35
    edge = math.exp(-0.5 * ((dist - radius) / max(edge_band, 1e-6)) ** 2)
    if mode == "filled":
        return inside
    if mode == "edge":
        return edge
    if mode == "hybrid":
        return 0.65 * inside + 0.35 * edge
    raise ValueError(f"Unknown template mode: {mode}")


def normalized_correlation(measured: list[float], expected: list[float]) -> float:
    if len(measured) < 3:
        return -1e9
    m_mean = sum(measured) / len(measured)
    e_mean = sum(expected) / len(expected)
    m0 = [v - m_mean for v in measured]
    e0 = [v - e_mean for v in expected]
    m_norm = math.sqrt(sum(v * v for v in m0))
    e_norm = math.sqrt(sum(v * v for v in e0))
    if m_norm == 0 or e_norm == 0:
        return -1e9
    return sum(a * b for a, b in zip(m0, e0)) / (m_norm * e_norm)


def robust_scale(values: list[float]) -> float:
    if not values:
        return 1.0
    med = median(values)
    deviations = [abs(v - med) for v in values]
    mad = median(deviations)
    scale = 1.4826 * mad
    if scale <= 1e-9:
        span = max(values) - min(values)
        scale = span / 6.0 if span > 0 else 1.0
    return max(scale, 1e-6)


def score_center(
    points: list[dict[str, float]],
    center_axis: float,
    center_circum: float,
    hole_radius_mm: float,
    edge_band_mm: float,
    template_mode: str,
    outside_margin_mm: float,
    min_points: int,
    score_mode: str,
    data_scale_mm: float,
) -> Optional[dict[str, float]]:
    measured: list[float] = []
    expected: list[float] = []
    positive_measured: list[float] = []
    positive_expected: list[float] = []
    inside: list[float] = []
    outside: list[float] = []
    edge: list[float] = []

    window_radius = hole_radius_mm + outside_margin_mm
    for p in points:
        dx = p["axis_mm"] - center_axis
        ds = p["circum_mm"] - center_circum
        dist = math.hypot(dx, ds)
        if dist > window_radius:
            continue

        outward = p["outward_mm"]
        tv = template_value(dist, hole_radius_mm, edge_band_mm, template_mode)
        measured.append(outward)
        expected.append(tv)
        positive_measured.append(max(0.0, outward))
        positive_expected.append(max(0.0, tv))

        if dist <= hole_radius_mm:
            inside.append(outward)
        else:
            outside.append(outward)
        if abs(dist - hole_radius_mm) <= edge_band_mm:
            edge.append(outward)

    if len(measured) < min_points or not inside or not outside:
        return None

    corr = normalized_correlation(measured, expected)
    positive_corr = normalized_correlation(positive_measured, positive_expected)
    contrast = (sum(inside) / len(inside)) - (sum(outside) / len(outside))
    edge_mean = sum(edge) / len(edge) if edge else 0.0
    inside_mean = sum(inside) / len(inside)
    outside_mean = sum(outside) / len(outside)
    inside_positive = [max(0.0, v) for v in inside]
    outside_positive = [max(0.0, v) for v in outside]
    inside_positive_mean = sum(inside_positive) / len(inside_positive)
    outside_positive_mean = sum(outside_positive) / len(outside_positive)
    positive_contrast = inside_positive_mean - outside_positive_mean
    red_fraction_inside = sum(1 for v in inside if v > 0.0) / len(inside)

    if score_mode == "signed":
        # Correlation judges the shape; contrast prevents selecting a circle
        # where both inside and outside are similarly flat.
        score = corr + 0.25 * contrast + 0.10 * edge_mean
    elif score_mode == "positive":
        # For this experiment, the physical dent must be positive outward.
        # Blue/negative regions should not be allowed to win just because their
        # shape correlates with the template.
        score = (
            positive_corr
            + 0.70 * (positive_contrast / data_scale_mm)
            + 0.20 * red_fraction_inside
        )
        if inside_mean <= outside_mean:
            score -= 1.0
        if inside_positive_mean <= 0.0:
            score -= 1.0
    else:
        raise ValueError(f"Unknown score mode: {score_mode}")

    return {
        "match_score": score,
        "correlation": corr,
        "positive_correlation": positive_corr,
        "inside_mean_outward_mm": inside_mean,
        "outside_mean_outward_mm": outside_mean,
        "edge_mean_outward_mm": edge_mean,
        "inside_points": float(len(inside)),
        "outside_points": float(len(outside)),
        "edge_points": float(len(edge)),
    }


def build_center_seeds(
    points: list[dict[str, float]],
    hole_radius_mm: float,
    grid_step_mm: Optional[float],
    search_top_fraction: float,
) -> list[tuple[float, float]]:
    axes = [p["axis_mm"] for p in points]
    circs = [p["circum_mm"] for p in points]
    if grid_step_mm is None:
        axis_step = median_axis_spacing_by_angle(points, 5.0)
        circum_step = median_spacing(circs, 30.0)
        grid_step_mm = max(5.0, min(axis_step * 2.0, circum_step))

    axis_min = min(axes) + hole_radius_mm * 0.25
    axis_max = max(axes) - hole_radius_mm * 0.25
    circum_min = min(circs) + hole_radius_mm * 0.25
    circum_max = max(circs) - hole_radius_mm * 0.25

    seeds: set[tuple[float, float]] = set()
    x = axis_min
    while x <= axis_max:
        s = circum_min
        while s <= circum_max:
            seeds.add((x, s))
            s += grid_step_mm
        x += grid_step_mm

    positive_points = sorted(points, key=lambda p: p["outward_mm"], reverse=True)
    top_n = max(20, int(len(positive_points) * search_top_fraction))
    for p in positive_points[:top_n]:
        seeds.add((p["axis_mm"], p["circum_mm"]))

    return sorted(seeds)


def find_best_match(
    points: list[dict[str, float]],
    hole_diameter_mm: float,
    edge_band_mm: float,
    outside_margin_mm: float,
    template_mode: str,
    min_points: int,
    grid_step_mm: Optional[float],
    search_top_fraction: float,
    score_mode: str,
) -> dict[str, float]:
    hole_radius = hole_diameter_mm / 2.0
    seeds = build_center_seeds(points, hole_radius, grid_step_mm, search_top_fraction)
    best: Optional[dict[str, float]] = None
    data_scale_mm = robust_scale([p["outward_mm"] for p in points])

    for center_axis, center_circum in seeds:
        result = score_center(
            points,
            center_axis=center_axis,
            center_circum=center_circum,
            hole_radius_mm=hole_radius,
            edge_band_mm=edge_band_mm,
            template_mode=template_mode,
            outside_margin_mm=outside_margin_mm,
            min_points=min_points,
            score_mode=score_mode,
            data_scale_mm=data_scale_mm,
        )
        if result is None:
            continue
        result.update(
            {
                "center_axis_mm": center_axis,
                "center_circum_mm": center_circum,
            }
        )
        if best is None or result["match_score"] > best["match_score"]:
            best = result

    if best is None:
        raise SystemExit("No valid template match found. Try lowering --min-window-points or changing --grid-step-mm.")
    return best


def annotate_points(
    points: list[dict[str, float]],
    center_axis: float,
    center_circum: float,
    hole_radius_mm: float,
    edge_band_mm: float,
    template_mode: str,
) -> list[dict[str, float]]:
    annotated: list[dict[str, float]] = []
    for p in points:
        dx = p["axis_mm"] - center_axis
        ds = p["circum_mm"] - center_circum
        dist = math.hypot(dx, ds)
        row = dict(p)
        row["template_value"] = template_value(dist, hole_radius_mm, edge_band_mm, template_mode)
        row["recognized_inside"] = 1.0 if dist <= hole_radius_mm else 0.0
        row["recognized_edge"] = 1.0 if abs(dist - hole_radius_mm) <= edge_band_mm else 0.0
        annotated.append(row)
    return annotated


def annotate_surface_points(
    points: list[dict[str, float]],
    center_axis: float,
    center_circum: float,
    hole_radius_mm: float,
    edge_band_mm: float,
    template_mode: str,
) -> list[dict[str, float]]:
    annotated: list[dict[str, float]] = []
    for p in points:
        dx = p["axis_mm"] - center_axis
        ds = p["circum_mm"] - center_circum
        dist = math.hypot(dx, ds)
        row = dict(p)
        row["template_value"] = template_value(dist, hole_radius_mm, edge_band_mm, template_mode)
        row["recognized_inside"] = 1.0 if dist <= hole_radius_mm else 0.0
        row["recognized_edge"] = 1.0 if abs(dist - hole_radius_mm) <= edge_band_mm else 0.0
        annotated.append(row)
    return annotated


def boundary_points(
    center_axis: float,
    center_circum: float,
    hole_radius_mm: float,
    pipe_radius_mm: float,
    base_radius_mm: float,
) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for i in range(181):
        t = 2.0 * math.pi * i / 180.0
        axis = center_axis + hole_radius_mm * math.cos(t)
        circum = center_circum + hole_radius_mm * math.sin(t)
        angle = math.degrees(circum / pipe_radius_mm)
        theta = math.radians(angle)
        out.append(
            {
                "boundary_index": float(i),
                "axis_mm": axis,
                "angle_deg": angle,
                "circum_mm": circum,
                "x_mm": axis,
                "y_mm": base_radius_mm * math.cos(theta),
                "z_mm": base_radius_mm * math.sin(theta),
            }
        )
    return out


def write_summary(summary: dict[str, float | str], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        out = {}
        for key in SUMMARY_COLUMNS:
            value = summary[key]
            out[key] = value if isinstance(value, str) else f"{float(value):.6f}"
        writer.writerow(out)


def write_points(points: list[dict[str, float]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=POINT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for p in points:
            writer.writerow({k: f"{p[k]:.6f}" for k in POINT_COLUMNS})


def write_boundary(boundary: list[dict[str, float]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=BOUNDARY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for p in boundary:
            writer.writerow({k: f"{p[k]:.6f}" for k in BOUNDARY_COLUMNS})


def write_html(
    points: list[dict[str, float]],
    boundary: list[dict[str, float]],
    summary: dict[str, float | str],
    input_path: Path,
    output_path: Path,
) -> None:
    inside_points = [p for p in points if p["recognized_inside"] == 1.0]
    edge_points = [p for p in points if p["recognized_edge"] == 1.0]

    payload = {
        "input": input_path.name,
        "summary": summary,
        "x": [p["x_mm"] for p in points],
        "y": [p["y_mm"] for p in points],
        "z": [p["z_mm"] for p in points],
        "axis": [p["axis_mm"] for p in points],
        "angle": [p["angle_deg"] for p in points],
        "outward": [p["outward_mm"] for p in points],
        "insideX": [p["x_mm"] for p in inside_points],
        "insideY": [p["y_mm"] for p in inside_points],
        "insideZ": [p["z_mm"] for p in inside_points],
        "edgeX": [p["x_mm"] for p in edge_points],
        "edgeY": [p["y_mm"] for p in edge_points],
        "edgeZ": [p["z_mm"] for p in edge_points],
        "boundaryX": [p["x_mm"] for p in boundary],
        "boundaryY": [p["y_mm"] for p in boundary],
        "boundaryZ": [p["z_mm"] for p in boundary],
        "boundaryAxis": [p["axis_mm"] for p in boundary],
        "boundaryAngle": [p["angle_deg"] for p in boundary],
    }

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Dent Template Match 3D</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ padding: 12px 16px; border-bottom: 1px solid #ddd; }}
    h1 {{ margin: 0 0 6px; font-size: 18px; }}
    .meta {{ color: #555; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 12px; padding: 12px; }}
    .plot {{ height: 58vh; min-height: 460px; }}
    #map2d {{ height: 46vh; min-height: 360px; }}
  </style>
</head>
<body>
  <header>
    <h1>Dent Template Match</h1>
    <div class="meta" id="meta"></div>
  </header>
  <div class="grid">
    <div id="before3d" class="plot"></div>
    <div id="after3d" class="plot"></div>
    <div id="map2d"></div>
  </div>
  <script>
    const data = {json.dumps(payload, ensure_ascii=False)};
    const s = data.summary;
    document.getElementById("meta").textContent =
      `${{data.input}} | template=${{s.template_mode}} | center axis=${{Number(s.center_axis_mm).toFixed(2)}} mm | center angle=${{Number(s.center_angle_deg).toFixed(2)}} deg | score=${{Number(s.match_score).toFixed(4)}}`;

    const baseCloud = {{
      type: "scatter3d",
      mode: "markers",
      x: data.x,
      y: data.y,
      z: data.z,
      marker: {{
        size: 3,
        color: data.outward,
        colorscale: "Turbo",
        colorbar: {{ title: "outward + mm" }},
        opacity: 0.85
      }},
      hovertemplate: "axis=%{{customdata[0]:.3f}} mm<br>angle=%{{customdata[1]:.3f}} deg<br>outward=%{{marker.color:.6f}} mm<extra></extra>",
      customdata: data.axis.map((v, i) => [v, data.angle[i]]),
      name: "measured"
    }};

    const layout3d = (title) => ({{
      title,
      scene: {{
        xaxis: {{ title: "pipe axis x [mm]" }},
        yaxis: {{ title: "y [mm]" }},
        zaxis: {{ title: "z [mm]" }},
        aspectmode: "data"
      }},
      margin: {{ l: 0, r: 0, t: 42, b: 0 }}
    }});

    Plotly.newPlot("before3d", [baseCloud], layout3d("Before recognition: measured 3D map"), {{ responsive: true }});

    Plotly.newPlot("after3d", [
      baseCloud,
      {{
        type: "scatter3d",
        mode: "markers",
        x: data.insideX,
        y: data.insideY,
        z: data.insideZ,
        marker: {{ size: 5, color: "white", opacity: 0.85, line: {{ color: "black", width: 1 }} }},
        name: "recognized dent area"
      }},
      {{
        type: "scatter3d",
        mode: "markers",
        x: data.edgeX,
        y: data.edgeY,
        z: data.edgeZ,
        marker: {{ size: 6, color: "black", opacity: 0.9 }},
        name: "recognized edge samples"
      }},
      {{
        type: "scatter3d",
        mode: "lines",
        x: data.boundaryX,
        y: data.boundaryY,
        z: data.boundaryZ,
        line: {{ color: "red", width: 7 }},
        name: "matched circle"
      }}
    ], layout3d("After recognition: matched circular dent"), {{ responsive: true }});

    Plotly.newPlot("map2d", [
      {{
        type: "scatter",
        mode: "markers",
        x: data.axis,
        y: data.angle,
        marker: {{
          size: 7,
          color: data.outward,
          colorscale: "Turbo",
          colorbar: {{ title: "outward + mm" }}
        }},
        hovertemplate: "axis=%{{x:.3f}} mm<br>angle=%{{y:.3f}} deg<br>outward=%{{marker.color:.6f}} mm<extra></extra>",
        name: "measured"
      }},
      {{
        type: "scatter",
        mode: "lines",
        x: data.boundaryAxis,
        y: data.boundaryAngle,
        line: {{ color: "red", width: 4 }},
        name: "matched circle"
      }}
    ], {{
      title: "Unwrapped map with matched circular dent",
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
    parser = argparse.ArgumentParser(description="Detect known circular dent by template matching.")
    parser.add_argument("--input", required=True, help="Filtered CSV from dent_filter_detect.py.")
    parser.add_argument("--value-column", default="radial_outward_mm")
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--base-radius-mm", type=float, default=120.0)
    parser.add_argument("--hole-diameter-mm", type=float, default=199.0)
    parser.add_argument("--template-mode", choices=["filled", "edge", "hybrid"], default="hybrid")
    parser.add_argument(
        "--score-mode",
        choices=["positive", "signed"],
        default="positive",
        help="positive uses only outward-positive evidence so blue/negative regions cannot win.",
    )
    parser.add_argument("--edge-band-mm", type=float, default=18.0)
    parser.add_argument("--outside-margin-mm", type=float, default=45.0)
    parser.add_argument("--grid-step-mm", type=float, default=None)
    parser.add_argument("--search-top-fraction", type=float, default=0.08)
    parser.add_argument("--min-window-points", type=int, default=30)
    parser.add_argument("--output-summary", default=None)
    parser.add_argument("--output-points", default=None)
    parser.add_argument("--output-boundary", default=None)
    parser.add_argument("--output-html", default=None)
    parser.add_argument(
        "--surface-input",
        default=None,
        help="Optional raw CSV used only for the before/after 3D display.",
    )
    parser.add_argument("--surface-lk-column", default=None)
    parser.add_argument("--surface-x-column", default=None)
    parser.add_argument("--surface-lk-reference", choices=["first", "mean", "median"], default="median")
    parser.add_argument("--surface-lk-reference-mm", type=float, default=None)
    parser.add_argument(
        "--surface-invert-lk",
        action="store_true",
        help="Use when raw LK decreases in the physical outward direction.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input CSV not found: {input_path}")
        return 1

    output_summary = (
        Path(args.output_summary)
        if args.output_summary
        else input_path.with_name(f"{input_path.stem}_template_match.csv")
    )
    output_points = (
        Path(args.output_points)
        if args.output_points
        else input_path.with_name(f"{input_path.stem}_template_points.csv")
    )
    output_boundary = (
        Path(args.output_boundary)
        if args.output_boundary
        else input_path.with_name(f"{input_path.stem}_template_boundary.csv")
    )
    output_html = (
        Path(args.output_html)
        if args.output_html
        else input_path.with_name(f"{input_path.stem}_template_match_3d.html")
    )

    points = load_points(input_path, args.value_column, args.pipe_radius_mm, args.base_radius_mm)
    if not points:
        print("No valid points found.")
        return 1

    best = find_best_match(
        points,
        hole_diameter_mm=args.hole_diameter_mm,
        edge_band_mm=args.edge_band_mm,
        outside_margin_mm=args.outside_margin_mm,
        template_mode=args.template_mode,
        min_points=args.min_window_points,
        grid_step_mm=args.grid_step_mm,
        search_top_fraction=args.search_top_fraction,
        score_mode=args.score_mode,
    )

    hole_radius = args.hole_diameter_mm / 2.0
    center_angle = math.degrees(best["center_circum_mm"] / args.pipe_radius_mm)
    summary: dict[str, float | str] = {
        "hole_diameter_mm": args.hole_diameter_mm,
        "hole_radius_mm": hole_radius,
        "pipe_radius_mm": args.pipe_radius_mm,
        "template_mode": args.template_mode,
        "center_axis_mm": best["center_axis_mm"],
        "center_angle_deg": center_angle,
        "center_circum_mm": best["center_circum_mm"],
        "match_score": best["match_score"],
        "inside_mean_outward_mm": best["inside_mean_outward_mm"],
        "outside_mean_outward_mm": best["outside_mean_outward_mm"],
        "edge_mean_outward_mm": best["edge_mean_outward_mm"],
        "inside_points": best["inside_points"],
        "outside_points": best["outside_points"],
        "edge_points": best["edge_points"],
        "estimated_angle_span_deg": math.degrees(args.hole_diameter_mm / args.pipe_radius_mm),
    }

    annotated_for_csv = annotate_points(
        points,
        center_axis=best["center_axis_mm"],
        center_circum=best["center_circum_mm"],
        hole_radius_mm=hole_radius,
        edge_band_mm=args.edge_band_mm,
        template_mode=args.template_mode,
    )
    if args.surface_input:
        surface_points = load_surface_points(
            Path(args.surface_input),
            pipe_radius_mm=args.pipe_radius_mm,
            base_radius_mm=args.base_radius_mm,
            lk_column=args.surface_lk_column,
            x_column=args.surface_x_column,
            lk_reference_mode=args.surface_lk_reference,
            lk_reference_mm=args.surface_lk_reference_mm,
            invert_lk=args.surface_invert_lk,
        )
        annotated_for_html = annotate_surface_points(
            surface_points,
            center_axis=best["center_axis_mm"],
            center_circum=best["center_circum_mm"],
            hole_radius_mm=hole_radius,
            edge_band_mm=args.edge_band_mm,
            template_mode=args.template_mode,
        )
    else:
        annotated_for_html = annotated_for_csv
    boundary = boundary_points(best["center_axis_mm"], best["center_circum_mm"], hole_radius, args.pipe_radius_mm, args.base_radius_mm)

    write_summary(summary, output_summary)
    write_points(annotated_for_csv, output_points)
    write_boundary(boundary, output_boundary)
    write_html(annotated_for_html, boundary, summary, input_path, output_html)

    print(f"Input: {input_path.resolve()}")
    print(f"Template: {args.template_mode}, hole diameter={args.hole_diameter_mm:.3f} mm")
    print(f"Center axis: {float(summary['center_axis_mm']):.3f} mm")
    print(f"Center angle: {float(summary['center_angle_deg']):.3f} deg")
    print(f"Match score: {float(summary['match_score']):.6f}")
    print(f"Inside mean outward: {float(summary['inside_mean_outward_mm']):.6f} mm")
    print(f"Outside mean outward: {float(summary['outside_mean_outward_mm']):.6f} mm")
    print(f"Summary CSV: {output_summary.resolve()}")
    print(f"Point CSV: {output_points.resolve()}")
    print(f"Boundary CSV: {output_boundary.resolve()}")
    print(f"HTML: {output_html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
