"""
Convert angle-step LK-G/DL50 logs into a pipe-inner-surface point cloud.

The log contains:
    angle_deg       Servo angle around the pipe axis.
    dl50_delta_mm   Axial slide distance measured by DL50 Hi.
    lk_out1_*_mm    Radial surface signal from LK-G85A.

This script produces:
    1. A point-cloud CSV with x/y/z coordinates.
    2. An interactive HTML file with a 3D point cloud and an unwrapped heatmap.

Geometry model:
    x = axial distance along the pipe
    theta = servo angle
    radius = base_radius_mm + radial_delta_mm

The LK-G value is usually relative, so radial_delta_mm is calculated from a
reference value. radial_delta_mm is positive in the physical outward radial
direction. In the current LK-G85A setup, the dent area appears as a stronger
negative LK-G value, so use --invert-lk to map that to outward positive.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import OrderedDict
from pathlib import Path
from statistics import mean, median
from typing import Iterable, Optional


PREFERRED_LK_COLUMNS = [
    "lk_out1_corrected_mm",
    "lk_out1_mm",
    "lk_out2_corrected_mm",
    "lk_out2_mm",
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


def reference_value(values: list[float], mode: str, explicit: Optional[float]) -> float:
    if explicit is not None:
        return explicit
    if not values:
        raise SystemExit("No valid LK-G values found.")
    if mode == "first":
        return values[0]
    if mode == "mean":
        return mean(values)
    if mode == "median":
        return median(values)
    raise SystemExit(f"Unknown reference mode: {mode}")


def angle_label(angle: float) -> str:
    return f"{angle:g}"


def load_points(
    input_path: Path,
    lk_column: str | None,
    x_column: str | None,
    base_radius_mm: float,
    angle_offset_deg: float,
    lk_reference_mode: str,
    lk_reference_mm: Optional[float],
    lk_scale: float,
    invert_lk: bool,
    use_first_per_angle_x: bool,
) -> tuple[list[dict[str, float | str]], str, str, float]:
    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        chosen_lk = pick_column(fieldnames, lk_column, PREFERRED_LK_COLUMNS)
        chosen_x = pick_column(fieldnames, x_column, ["dl50_delta_mm", "target_delta_mm", "dl50_hi_mm"])
        rows = list(reader)

    raw_points: list[dict[str, float | str]] = []
    lk_values: list[float] = []
    seen: set[tuple[int, int]] = set()

    for row in rows:
        angle = parse_float(row.get("angle_deg"))
        x_mm = parse_float(row.get(chosen_x))
        lk_mm = parse_float(row.get(chosen_lk))
        if angle is None or x_mm is None or lk_mm is None:
            continue

        angle_key = round(angle * 1000)
        x_key = round(x_mm * 1000)
        if use_first_per_angle_x:
            key = (angle_key, x_key)
            if key in seen:
                continue
            seen.add(key)

        raw_points.append(
            {
                "angle_deg": angle,
                "axis_mm": x_mm,
                "lk_mm": lk_mm,
                "pc_time": row.get("pc_time", ""),
                "angle_group": row.get("angle_group", ""),
                "trigger_index": row.get("trigger_index", ""),
                "target_delta_mm": row.get("target_delta_mm", ""),
            }
        )
        lk_values.append(lk_mm)

    ref = reference_value(lk_values, lk_reference_mode, lk_reference_mm)
    sign = -1.0 if invert_lk else 1.0

    points: list[dict[str, float | str]] = []
    for point in raw_points:
        angle = float(point["angle_deg"])
        axis_mm = float(point["axis_mm"])
        lk_mm = float(point["lk_mm"])
        radial_delta_mm = sign * (lk_mm - ref) * lk_scale
        radius_mm = base_radius_mm + radial_delta_mm
        theta = math.radians(angle + angle_offset_deg)
        y_mm = radius_mm * math.cos(theta)
        z_mm = radius_mm * math.sin(theta)

        out = dict(point)
        out.update(
            {
                "x_mm": axis_mm,
                "y_mm": y_mm,
                "z_mm": z_mm,
                "radius_mm": radius_mm,
                "radial_delta_mm": radial_delta_mm,
                "outward_mm": radial_delta_mm,
                "depth_mm": radial_delta_mm,
                "theta_deg": angle + angle_offset_deg,
                "lk_reference_mm": ref,
            }
        )
        points.append(out)

    points.sort(key=lambda p: (float(p["angle_deg"]), float(p["axis_mm"])))
    return points, chosen_lk, chosen_x, ref


def write_point_cloud(points: list[dict[str, float | str]], output_path: Path) -> None:
    columns = [
        "x_mm",
        "y_mm",
        "z_mm",
        "radius_mm",
        "radial_delta_mm",
        "outward_mm",
        "depth_mm",
        "angle_deg",
        "theta_deg",
        "axis_mm",
        "lk_mm",
        "lk_reference_mm",
        "pc_time",
        "angle_group",
        "trigger_index",
        "target_delta_mm",
    ]
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(points)


def build_heatmap(points: list[dict[str, float | str]]) -> dict[str, object]:
    angles = list(OrderedDict((float(p["angle_deg"]), None) for p in points).keys())
    axes = list(OrderedDict((float(p["axis_mm"]), None) for p in points).keys())
    angles.sort()
    axes.sort()

    by_key: dict[tuple[float, float], float] = {}
    for p in points:
        by_key[(float(p["angle_deg"]), float(p["axis_mm"]))] = float(p["outward_mm"])

    z: list[list[Optional[float]]] = []
    for angle in angles:
        row: list[Optional[float]] = []
        for axis in axes:
            row.append(by_key.get((angle, axis)))
        z.append(row)

    return {
        "angles": [angle_label(a) for a in angles],
        "axes": axes,
        "z": z,
    }


def write_html(
    points: list[dict[str, float | str]],
    output_path: Path,
    input_path: Path,
    chosen_lk: str,
    chosen_x: str,
    lk_reference: float,
) -> None:
    xs = [float(p["x_mm"]) for p in points]
    ys = [float(p["y_mm"]) for p in points]
    zs = [float(p["z_mm"]) for p in points]
    outward = [float(p["outward_mm"]) for p in points]
    hover = [
        (
            f"angle={float(p['angle_deg']):.3f} deg"
            f"<br>axis={float(p['axis_mm']):.3f} mm"
            f"<br>LK={float(p['lk_mm']):.6f} mm"
            f"<br>outward={float(p['outward_mm']):.6f} mm"
        )
        for p in points
    ]
    heatmap = build_heatmap(points)

    payload = {
        "xs": xs,
        "ys": ys,
        "zs": zs,
        "depth": outward,
        "hover": hover,
        "heatmap": heatmap,
        "title": f"{input_path.name} / {chosen_lk}",
        "chosenX": chosen_x,
        "chosenLk": chosen_lk,
        "lkReference": lk_reference,
        "pointCount": len(points),
    }

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Pipe Surface Visualization</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ padding: 12px 16px; border-bottom: 1px solid #ddd; }}
    h1 {{ margin: 0 0 6px; font-size: 18px; }}
    .meta {{ color: #555; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 12px; padding: 12px; }}
    #cloud {{ height: 62vh; min-height: 480px; }}
    #heatmap {{ height: 42vh; min-height: 340px; }}
  </style>
</head>
<body>
  <header>
    <h1>Pipe Surface Visualization</h1>
    <div class="meta" id="meta"></div>
  </header>
  <div class="grid">
    <div id="cloud"></div>
    <div id="heatmap"></div>
  </div>
  <script>
    const data = {json.dumps(payload, ensure_ascii=False)};
    document.getElementById("meta").textContent =
      `${{data.title}} | points=${{data.pointCount}} | x=${{data.chosenX}} | LK=${{data.chosenLk}} | LK ref=${{data.lkReference.toFixed(6)}} mm`;

    Plotly.newPlot("cloud", [{{
      type: "scatter3d",
      mode: "markers",
      x: data.xs,
      y: data.ys,
      z: data.zs,
      text: data.hover,
      hoverinfo: "text",
      marker: {{
        size: 4,
        color: data.depth,
        colorscale: "Turbo",
        colorbar: {{ title: "outward + mm" }},
        opacity: 0.9
      }}
    }}], {{
      title: "3D point cloud",
      scene: {{
        xaxis: {{ title: "pipe axis x [mm]" }},
        yaxis: {{ title: "y [mm]" }},
        zaxis: {{ title: "z [mm]" }},
        aspectmode: "data"
      }},
      margin: {{ l: 0, r: 0, t: 42, b: 0 }}
    }}, {{ responsive: true }});

    Plotly.newPlot("heatmap", [{{
      type: "heatmap",
      x: data.heatmap.axes,
      y: data.heatmap.angles,
      z: data.heatmap.z,
      colorscale: "Turbo",
      colorbar: {{ title: "outward + mm" }},
      hovertemplate: "axis=%{{x:.3f}} mm<br>angle=%{{y}} deg<br>outward +%{{z:.6f}} mm<extra></extra>"
    }}], {{
      title: "Unwrapped pipe map",
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
    parser = argparse.ArgumentParser(description="Visualize pipe inner-surface data from angle-step logs.")
    parser.add_argument("--input", required=True, help="Angle-step log CSV.")
    parser.add_argument("--output-html", default=None)
    parser.add_argument("--output-points", default=None)
    parser.add_argument("--lk-column", default=None, help="Default: lk_out1_corrected_mm, then lk_out1_mm.")
    parser.add_argument("--x-column", default=None, help="Default: dl50_delta_mm, then target_delta_mm.")
    parser.add_argument("--base-radius-mm", type=float, default=50.0)
    parser.add_argument("--angle-offset-deg", type=float, default=0.0)
    parser.add_argument("--lk-reference", choices=["first", "mean", "median"], default="median")
    parser.add_argument("--lk-reference-mm", type=float, default=None)
    parser.add_argument("--lk-scale", type=float, default=1.0)
    parser.add_argument("--invert-lk", action="store_true", help="Reverse the LK-G radial direction.")
    parser.add_argument(
        "--keep-duplicates",
        action="store_true",
        help="Keep repeated angle/x samples instead of using the first one.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input CSV not found: {input_path}")
        return 1

    output_html = Path(args.output_html) if args.output_html else input_path.with_name(f"{input_path.stem}_pipe_surface.html")
    output_points = (
        Path(args.output_points) if args.output_points else input_path.with_name(f"{input_path.stem}_point_cloud.csv")
    )

    points, chosen_lk, chosen_x, ref = load_points(
        input_path=input_path,
        lk_column=args.lk_column,
        x_column=args.x_column,
        base_radius_mm=args.base_radius_mm,
        angle_offset_deg=args.angle_offset_deg,
        lk_reference_mode=args.lk_reference,
        lk_reference_mm=args.lk_reference_mm,
        lk_scale=args.lk_scale,
        invert_lk=args.invert_lk,
        use_first_per_angle_x=not args.keep_duplicates,
    )

    if not points:
        print("No valid points were created. Check angle_deg, DL50, and LK-G columns.")
        return 1

    write_point_cloud(points, output_points)
    write_html(points, output_html, input_path, chosen_lk, chosen_x, ref)

    angles = sorted({float(p["angle_deg"]) for p in points})
    axes = [float(p["axis_mm"]) for p in points]
    depths = [float(p["outward_mm"]) for p in points]

    print(f"Input: {input_path.resolve()}")
    print(f"LK column: {chosen_lk}")
    print(f"X column: {chosen_x}")
    print(f"LK reference: {ref:.6f} mm")
    print(f"Points: {len(points)}")
    print(f"Angles: {', '.join(angle_label(a) for a in angles)} deg")
    print(f"Axis range: {min(axes):.3f} to {max(axes):.3f} mm")
    print(f"Outward range: {min(depths):.6f} to {max(depths):.6f} mm")
    print(f"Point cloud CSV: {output_points.resolve()}")
    print(f"HTML: {output_html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
