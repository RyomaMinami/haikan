"""
Apply a circular-edge match result to an axis-corrected point cloud.

This marks where the dent is on the corrected point cloud:
    recognized_inside = point is inside the matched circular dent area
    recognized_edge   = point is near the matched circular boundary

Inputs:
    1. Axis-corrected point cloud CSV from axis_correct_point_cloud.py
    2. Match summary CSV from axis_edge_template_match.py

Outputs:
    1. Point CSV with dent flags
    2. Interactive HTML with the full corrected point cloud and dent overlay
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Optional


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


def read_summary(path: Path) -> dict[str, float]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise SystemExit(f"Empty summary file: {path}")
    row = rows[0]
    keys = ["center_axis_s_mm", "center_angle_deg", "hole_radius_mm", "pipe_radius_mm"]
    out: dict[str, float] = {}
    for key in keys:
        value = parse_float(row.get(key))
        if value is None:
            raise SystemExit(f"Missing value in summary: {key}")
        out[key] = value
    return out


def read_points(path: Path) -> list[dict[str, object]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    points: list[dict[str, object]] = []
    for row in rows:
        axis = parse_float(row.get("axis_s_mm"))
        angle = parse_float(row.get("angle_deg"))
        y = parse_float(row.get("axis_y_mm"))
        z = parse_float(row.get("axis_z_mm"))
        outward = parse_float(row.get("fitted_outward_mm"))
        if axis is None or angle is None or y is None or z is None or outward is None:
            continue
        out: dict[str, object] = dict(row)
        out["axis_s_mm"] = axis
        out["angle_deg"] = angle
        out["axis_y_mm"] = y
        out["axis_z_mm"] = z
        out["fitted_outward_mm"] = outward
        points.append(out)
    if not points:
        raise SystemExit(f"No usable axis-corrected points found: {path}")
    return points


def classify_points(
    points: list[dict[str, object]],
    center_axis: float,
    center_angle: float,
    hole_radius: float,
    pipe_radius: float,
    edge_band_mm: float,
) -> list[dict[str, object]]:
    center_circ = pipe_radius * math.radians(center_angle)
    for point in points:
        axis = float(point["axis_s_mm"])
        angle = float(point["angle_deg"])
        circum = pipe_radius * math.radians(angle)
        d_axis = axis - center_axis
        d_circ = circum - center_circ
        dist = math.sqrt(d_axis * d_axis + d_circ * d_circ)
        inside = dist <= hole_radius
        edge = abs(dist - hole_radius) <= edge_band_mm
        point["dent_center_axis_s_mm"] = center_axis
        point["dent_center_angle_deg"] = center_angle
        point["dent_distance_mm"] = dist
        point["recognized_inside"] = int(inside)
        point["recognized_edge"] = int(edge)
        point["dent_region"] = "edge" if edge else ("inside" if inside else "outside")
    return points


def add_zero_based_x(points: list[dict[str, object]]) -> float:
    axis_min = min(float(point["axis_s_mm"]) for point in points)
    for point in points:
        point["x_zero_mm"] = float(point["axis_s_mm"]) - axis_min
        point["coordinate_axis_min_mm"] = axis_min
    return axis_min


def write_csv(points: list[dict[str, object]], path: Path) -> None:
    preferred = [
        "x_zero_mm",
        "axis_s_mm",
        "axis_y_mm",
        "axis_z_mm",
        "fitted_radius_mm",
        "fitted_outward_mm",
        "angle_deg",
        "dent_distance_mm",
        "recognized_inside",
        "recognized_edge",
        "dent_region",
        "dent_center_axis_s_mm",
        "dent_center_angle_deg",
        "coordinate_axis_min_mm",
    ]
    fields = list(dict.fromkeys(preferred + [k for p in points for k in p.keys()]))
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(points)


def boundary_points(center_axis: float, center_angle: float, hole_radius: float, pipe_radius: float, base_radius: float) -> list[dict[str, float]]:
    rows = []
    center_circ = pipe_radius * math.radians(center_angle)
    for k in range(240):
        t = 2.0 * math.pi * k / 240
        axis = center_axis + hole_radius * math.cos(t)
        circum = center_circ + hole_radius * math.sin(t)
        angle = math.degrees(circum / pipe_radius)
        theta = math.radians(angle)
        rows.append(
            {
                "axis_s_mm": axis,
                "angle_deg": angle,
                "axis_y_mm": base_radius * math.cos(theta),
                "axis_z_mm": base_radius * math.sin(theta),
            }
        )
    return rows


def write_html(points: list[dict[str, object]], boundary: list[dict[str, float]], path: Path, title: str) -> None:
    inside = [p for p in points if int(p["recognized_inside"]) == 1 and int(p["recognized_edge"]) == 0]
    edge = [p for p in points if int(p["recognized_edge"]) == 1]
    outside = [p for p in points if int(p["recognized_inside"]) == 0 and int(p["recognized_edge"]) == 0]

    def arr(rows: list[dict[str, object]], key: str) -> list[float]:
        return [float(r[key]) for r in rows]

    axis_min = add_zero_based_x(points)
    axis_values = arr(points, "x_zero_mm")
    y_values = arr(points, "axis_y_mm")
    z_values = arr(points, "axis_z_mm")

    payload = {
        "outside": {
            "x": arr(outside, "x_zero_mm"),
            "y": arr(outside, "axis_y_mm"),
            "z": arr(outside, "axis_z_mm"),
            "c": arr(outside, "fitted_outward_mm"),
        },
        "inside": {
            "x": arr(inside, "x_zero_mm"),
            "y": arr(inside, "axis_y_mm"),
            "z": arr(inside, "axis_z_mm"),
            "c": arr(inside, "fitted_outward_mm"),
        },
        "edge": {
            "x": arr(edge, "x_zero_mm"),
            "y": arr(edge, "axis_y_mm"),
            "z": arr(edge, "axis_z_mm"),
            "c": arr(edge, "fitted_outward_mm"),
        },
        "boundary": {
            "x": [r["axis_s_mm"] - axis_min for r in boundary],
            "y": [r["axis_y_mm"] for r in boundary],
            "z": [r["axis_z_mm"] for r in boundary],
            "angle": [r["angle_deg"] for r in boundary],
        },
        "counts": {"outside": len(outside), "inside": len(inside), "edge": len(edge)},
        "axisRange": [0.0, max(axis_values)],
        "yRange": [min(y_values), max(y_values)],
        "zRange": [min(z_values), max(z_values)],
        "axisZeroOffset": axis_min,
    }

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f7f7f5; color: #222; }}
    header {{ padding: 14px 18px; background: #fff; border-bottom: 1px solid #d7d7d2; }}
    h1 {{ margin: 0 0 6px; font-size: 18px; }}
    p {{ display: none; }}
    .note {{ margin: 0; color: #555; font-size: 13px; }}
    #plot {{ height: calc(100vh - 72px); min-height: 620px; }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <div class="note">配管軸方向の左端を0にした x mm / axis_y_mm / axis_z_mm 座標で表示しています。</div>
    <p>円形エッジ評価で推定した窪み位置を、軸補正後点群上に重ねています。</p>
  </header>
  <div id="plot"></div>
  <script>
    const d = {json.dumps(payload)};
    const traces = [
      {{
        type: 'scatter3d', mode: 'markers',
        x: d.outside.x, y: d.outside.y, z: d.outside.z,
        marker: {{size: 1.7, color: d.outside.c, colorscale: 'RdBu', reversescale: true, opacity: 0.42, colorbar: {{title:'out mm'}}}},
        name: `outside (${{d.counts.outside}})`
      }},
      {{
        type: 'scatter3d', mode: 'markers',
        x: d.inside.x, y: d.inside.y, z: d.inside.z,
        marker: {{size: 3.2, color: '#ff9d00', opacity: 0.85}},
        name: `detected dent area (${{d.counts.inside}})`
      }},
      {{
        type: 'scatter3d', mode: 'markers',
        x: d.edge.x, y: d.edge.y, z: d.edge.z,
        marker: {{size: 4.2, color: '#ff2020', opacity: 0.95}},
        name: `detected edge band (${{d.counts.edge}})`
      }},
      {{
        type: 'scatter3d', mode: 'lines',
        x: d.boundary.x, y: d.boundary.y, z: d.boundary.z,
        line: {{color: '#111', width: 7}},
        name: '199mm circular boundary'
      }}
    ];
    Plotly.newPlot('plot', traces, {{
      scene: {{
        xaxis: {{title:'x mm', range: d.axisRange, autorange: false}},
        yaxis: {{title:'axis y mm', range: d.yRange, autorange: false}},
        zaxis: {{title:'axis z mm', range: d.zRange, autorange: false}},
        camera: {{
          eye: {{x: 1.45, y: 1.45, z: 0.95}},
          up: {{x: 0, y: 0, z: 1}}
        }},
        aspectmode: 'data'
      }},
      margin: {{l:0,r:0,t:8,b:0}},
      legend: {{x: 0.02, y: 0.98}}
    }}, {{responsive:true}});
  </script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def default_path(point_path: Path, suffix: str) -> Path:
    stem = point_path.stem
    if stem.endswith("_point_cloud_axis_corrected"):
        stem = stem[: -len("_point_cloud_axis_corrected")]
    return point_path.with_name(f"{stem}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Mark detected circular dent region on axis-corrected point cloud.")
    parser.add_argument("--points", required=True, help="Axis-corrected point cloud CSV")
    parser.add_argument("--summary", required=True, help="Circular edge match summary CSV")
    parser.add_argument("--edge-band-mm", type=float, default=5.0)
    parser.add_argument("--base-radius-mm", type=float, default=120.0)
    parser.add_argument("--output-csv")
    parser.add_argument("--output-html")
    args = parser.parse_args()

    point_path = Path(args.points)
    summary_path = Path(args.summary)
    output_csv = Path(args.output_csv) if args.output_csv else default_path(point_path, "_detected_dent_region.csv")
    output_html = Path(args.output_html) if args.output_html else default_path(point_path, "_detected_dent_region.html")

    summary = read_summary(summary_path)
    points = read_points(point_path)
    points = classify_points(
        points,
        center_axis=summary["center_axis_s_mm"],
        center_angle=summary["center_angle_deg"],
        hole_radius=summary["hole_radius_mm"],
        pipe_radius=summary["pipe_radius_mm"],
        edge_band_mm=args.edge_band_mm,
    )
    axis_min = add_zero_based_x(points)
    boundary = boundary_points(
        summary["center_axis_s_mm"],
        summary["center_angle_deg"],
        summary["hole_radius_mm"],
        summary["pipe_radius_mm"],
        args.base_radius_mm,
    )
    write_csv(points, output_csv)
    write_html(points, boundary, output_html, f"Detected Dent Region: {point_path.name}")

    inside = sum(1 for p in points if int(p["recognized_inside"]) == 1)
    edge = sum(1 for p in points if int(p["recognized_edge"]) == 1)
    center_x_zero = summary["center_axis_s_mm"] - axis_min
    print(f"Center axis_s: {summary['center_axis_s_mm']:.3f} mm")
    print(f"Center x_zero: {center_x_zero:.3f} mm")
    print(f"Center angle: {summary['center_angle_deg']:.3f} deg")
    print(f"Hole radius: {summary['hole_radius_mm']:.3f} mm")
    print(f"Inside points: {inside}")
    print(f"Edge-band points: {edge}")
    print(f"Wrote CSV: {output_csv}")
    print(f"Wrote HTML: {output_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
