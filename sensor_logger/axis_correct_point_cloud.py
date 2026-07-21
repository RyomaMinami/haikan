"""
Estimate the pipe axis from a measured point cloud and rebuild coordinates.

Input is a point-cloud CSV produced by pipe_surface_visualizer.py. The original
visualizer assumes the servo rotation axis is the pipe axis. This script
estimates the pipe axis from the measured points themselves:

1. Split points along the original x axis.
2. Fit a circle to each y-z cross section to estimate local pipe centers.
3. Fit a 3D line through those centers as the pipe axis.
4. For every point, calculate distance from the fitted axis and output a
   corrected point cloud in an axis-based coordinate frame.

The corrected coordinate frame is:
    axis_s_mm      position along the fitted pipe axis
    axis_y_mm/z_mm coordinates perpendicular to the fitted pipe axis
    fitted_radius_mm distance from fitted pipe axis
    fitted_outward_mm fitted_radius_mm - reference_radius_mm
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Optional

import numpy as np


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


def load_points(path: Path) -> list[dict[str, object]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            x = parse_float(row.get("x_mm"))
            y = parse_float(row.get("y_mm"))
            z = parse_float(row.get("z_mm"))
            if x is None or y is None or z is None:
                continue
            out: dict[str, object] = dict(row)
            out["x_mm"] = x
            out["y_mm"] = y
            out["z_mm"] = z
            rows.append(out)
    if not rows:
        raise SystemExit(f"No usable x_mm/y_mm/z_mm points found: {path}")
    return rows


def fit_circle_yz(points: np.ndarray) -> tuple[float, float, float, float]:
    """Return center_y, center_z, radius, RMS residual."""
    y = points[:, 1]
    z = points[:, 2]
    a = np.column_stack([y, z, np.ones_like(y)])
    b = -(y * y + z * z)
    d, e, f = np.linalg.lstsq(a, b, rcond=None)[0]
    cy = -d / 2.0
    cz = -e / 2.0
    radius_sq = cy * cy + cz * cz - f
    radius = math.sqrt(max(radius_sq, 0.0))
    residual = np.sqrt(np.mean((np.sqrt((y - cy) ** 2 + (z - cz) ** 2) - radius) ** 2))
    return cy, cz, radius, float(residual)


def robust_fit_circle_yz(
    points: np.ndarray,
    trim_sigma: float,
    iterations: int,
    min_points: int,
) -> tuple[float, float, float, float, int]:
    work = points
    last = fit_circle_yz(work)
    for _ in range(iterations):
        cy, cz, radius, _ = fit_circle_yz(work)
        dist = np.sqrt((work[:, 1] - cy) ** 2 + (work[:, 2] - cz) ** 2)
        residual = np.abs(dist - radius)
        med = float(np.median(residual))
        mad = float(np.median(np.abs(residual - med)))
        scale = 1.4826 * mad if mad > 1e-9 else float(np.std(residual))
        if scale <= 1e-9:
            last = fit_circle_yz(work)
            break
        keep = residual <= med + trim_sigma * scale
        if int(np.count_nonzero(keep)) < min_points or np.all(keep):
            last = fit_circle_yz(work)
            break
        work = work[keep]
        last = fit_circle_yz(work)
    cy, cz, radius, rms = last
    return cy, cz, radius, rms, int(len(work))


def estimate_section_centers(
    points_xyz: np.ndarray,
    bin_mm: float,
    min_points: int,
    trim_sigma: float,
    robust_iterations: int,
) -> list[dict[str, float]]:
    x_min = float(np.min(points_xyz[:, 0]))
    x_max = float(np.max(points_xyz[:, 0]))
    centers: list[dict[str, float]] = []
    start = math.floor(x_min / bin_mm) * bin_mm
    x = start
    while x <= x_max:
        mask = (points_xyz[:, 0] >= x) & (points_xyz[:, 0] < x + bin_mm)
        section = points_xyz[mask]
        if len(section) >= min_points:
            try:
                cy, cz, radius, rms, used = robust_fit_circle_yz(
                    section,
                    trim_sigma=trim_sigma,
                    iterations=robust_iterations,
                    min_points=min_points,
                )
                centers.append(
                    {
                        "section_x_mm": float(np.mean(section[:, 0])),
                        "center_y_mm": cy,
                        "center_z_mm": cz,
                        "section_radius_mm": radius,
                        "fit_rms_mm": rms,
                        "points_used": float(used),
                    }
                )
            except np.linalg.LinAlgError:
                pass
        x += bin_mm
    if len(centers) < 2:
        raise SystemExit("Could not estimate enough section centers. Increase --section-bin-mm or lower --min-section-points.")
    return centers


def fit_axis_line(centers: list[dict[str, float]]) -> tuple[np.ndarray, np.ndarray]:
    center_xyz = np.array(
        [[c["section_x_mm"], c["center_y_mm"], c["center_z_mm"]] for c in centers],
        dtype=float,
    )
    origin = np.mean(center_xyz, axis=0)
    _, _, vh = np.linalg.svd(center_xyz - origin, full_matrices=False)
    direction = vh[0]
    if direction[0] < 0:
        direction = -direction
    direction = direction / np.linalg.norm(direction)
    return origin, direction


def perpendicular_basis(axis_direction: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    reference = np.array([0.0, 0.0, 1.0])
    if abs(float(np.dot(reference, axis_direction))) > 0.9:
        reference = np.array([0.0, 1.0, 0.0])
    e1 = np.cross(reference, axis_direction)
    e1 = e1 / np.linalg.norm(e1)
    e2 = np.cross(axis_direction, e1)
    e2 = e2 / np.linalg.norm(e2)
    return e1, e2


def correct_points(
    rows: list[dict[str, object]],
    axis_origin: np.ndarray,
    axis_direction: np.ndarray,
    reference_radius_mm: Optional[float],
) -> tuple[list[dict[str, object]], float]:
    e1, e2 = perpendicular_basis(axis_direction)
    xyz = np.array([[float(r["x_mm"]), float(r["y_mm"]), float(r["z_mm"])] for r in rows], dtype=float)
    relative = xyz - axis_origin
    s = relative @ axis_direction
    closest = axis_origin + np.outer(s, axis_direction)
    radial_vec = xyz - closest
    radial_y = radial_vec @ e1
    radial_z = radial_vec @ e2
    radii = np.sqrt(radial_y * radial_y + radial_z * radial_z)
    radius_ref = float(reference_radius_mm) if reference_radius_mm is not None else float(np.median(radii))

    corrected: list[dict[str, object]] = []
    for i, row in enumerate(rows):
        theta = math.degrees(math.atan2(float(radial_z[i]), float(radial_y[i])))
        out = dict(row)
        out.update(
            {
                "axis_s_mm": float(s[i]),
                "axis_y_mm": float(radial_y[i]),
                "axis_z_mm": float(radial_z[i]),
                "fitted_radius_mm": float(radii[i]),
                "fitted_outward_mm": float(radii[i] - radius_ref),
                "fitted_theta_deg": theta,
                "axis_closest_x_mm": float(closest[i, 0]),
                "axis_closest_y_mm": float(closest[i, 1]),
                "axis_closest_z_mm": float(closest[i, 2]),
                "axis_origin_x_mm": float(axis_origin[0]),
                "axis_origin_y_mm": float(axis_origin[1]),
                "axis_origin_z_mm": float(axis_origin[2]),
                "axis_dir_x": float(axis_direction[0]),
                "axis_dir_y": float(axis_direction[1]),
                "axis_dir_z": float(axis_direction[2]),
                "reference_radius_mm": radius_ref,
            }
        )
        corrected.append(out)
    s_min = min(float(p["axis_s_mm"]) for p in corrected)
    for point in corrected:
        point["x_zero_mm"] = float(point["axis_s_mm"]) - s_min
        point["coordinate_axis_min_mm"] = s_min
    corrected.sort(key=lambda p: (float(p["fitted_theta_deg"]), float(p["axis_s_mm"])))
    return corrected, radius_ref


def write_csv(rows: list[dict[str, object]], path: Path) -> None:
    preferred = [
        "x_zero_mm",
        "axis_s_mm",
        "axis_y_mm",
        "axis_z_mm",
        "fitted_radius_mm",
        "fitted_outward_mm",
        "fitted_theta_deg",
        "x_mm",
        "y_mm",
        "z_mm",
        "radius_mm",
        "radial_delta_mm",
        "outward_mm",
        "angle_deg",
        "theta_deg",
        "axis_mm",
        "lk_mm",
        "reference_radius_mm",
        "coordinate_axis_min_mm",
        "axis_closest_x_mm",
        "axis_closest_y_mm",
        "axis_closest_z_mm",
        "axis_origin_x_mm",
        "axis_origin_y_mm",
        "axis_origin_z_mm",
        "axis_dir_x",
        "axis_dir_y",
        "axis_dir_z",
    ]
    fieldnames = list(dict.fromkeys(preferred + [k for row in rows for k in row.keys()]))
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_centers_csv(centers: list[dict[str, float]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["section_x_mm", "center_y_mm", "center_z_mm", "section_radius_mm", "fit_rms_mm", "points_used"],
        )
        writer.writeheader()
        writer.writerows(centers)


def html_float_list(rows: list[dict[str, object]], key: str) -> list[float]:
    return [float(r[key]) for r in rows]


def write_html(rows: list[dict[str, object]], centers: list[dict[str, float]], output_path: Path, title: str) -> None:
    plot_data = {
        "orig_x": html_float_list(rows, "x_mm"),
        "orig_y": html_float_list(rows, "y_mm"),
        "orig_z": html_float_list(rows, "z_mm"),
        "corr_x": html_float_list(rows, "axis_s_mm"),
        "corr_x_zero": html_float_list(rows, "x_zero_mm"),
        "corr_y": html_float_list(rows, "axis_y_mm"),
        "corr_z": html_float_list(rows, "axis_z_mm"),
        "outward": html_float_list(rows, "fitted_outward_mm"),
        "angle": [str(r.get("angle_deg", "")) for r in rows],
        "center_x": [c["section_x_mm"] for c in centers],
        "center_y": [c["center_y_mm"] for c in centers],
        "center_z": [c["center_z_mm"] for c in centers],
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
    header {{ padding: 14px 18px; border-bottom: 1px solid #d7d7d2; background: #fff; }}
    h1 {{ margin: 0; font-size: 18px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px; }}
    .plot {{ min-height: 560px; background: #fff; border: 1px solid #ddd; }}
    @media (max-width: 980px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header><h1>{title}</h1></header>
  <div class="grid">
    <div id="original" class="plot"></div>
    <div id="corrected" class="plot"></div>
  </div>
  <script>
    const data = {json.dumps(plot_data)};
    const colorScale = 'RdBu';
    Plotly.newPlot('original', [
      {{
        type: 'scatter3d', mode: 'markers',
        x: data.orig_x, y: data.orig_y, z: data.orig_z,
        marker: {{ size: 2, color: data.outward, colorscale: colorScale, reversescale: true, colorbar: {{title: 'axis outward mm'}} }},
        text: data.angle.map((a, i) => `angle=${{a}}<br>out=${{data.outward[i].toFixed(3)}} mm`),
        name: 'original points'
      }},
      {{
        type: 'scatter3d', mode: 'markers+lines',
        x: data.center_x, y: data.center_y, z: data.center_z,
        marker: {{ size: 4, color: '#111' }},
        line: {{ color: '#111', width: 4 }},
        name: 'estimated centers'
      }}
    ], {{
      title: 'Original coordinates + estimated section centers',
      scene: {{ xaxis: {{title:'x mm'}}, yaxis: {{title:'y mm'}}, zaxis: {{title:'z mm'}}, aspectmode: 'data' }},
      margin: {{l:0,r:0,t:40,b:0}}
    }}, {{responsive:true}});

    Plotly.newPlot('corrected', [{{
      type: 'scatter3d', mode: 'markers',
      x: data.corr_x_zero, y: data.corr_y, z: data.corr_z,
      marker: {{ size: 2, color: data.outward, colorscale: colorScale, reversescale: true, colorbar: {{title: 'axis outward mm'}} }},
      text: data.angle.map((a, i) => `angle=${{a}}<br>x=${{data.corr_x_zero[i].toFixed(1)}} mm<br>axis_s=${{data.corr_x[i].toFixed(1)}} mm<br>r-out=${{data.outward[i].toFixed(3)}} mm`),
      name: 'axis corrected points'
    }}], {{
      title: 'Axis-corrected point cloud',
      scene: {{ xaxis: {{title:'x mm'}}, yaxis: {{title:'axis y mm'}}, zaxis: {{title:'axis z mm'}}, aspectmode: 'data' }},
      margin: {{l:0,r:0,t:40,b:0}}
    }}, {{responsive:true}});
  </script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def default_output(input_path: Path, suffix: str) -> Path:
    return input_path.with_name(f"{input_path.stem}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate pipe axis and rebuild point cloud coordinates.")
    parser.add_argument("--input", required=True, help="Point-cloud CSV from pipe_surface_visualizer.py")
    parser.add_argument("--output-csv", help="Corrected point-cloud CSV")
    parser.add_argument("--output-centers", help="Estimated cross-section centers CSV")
    parser.add_argument("--output-html", help="3D comparison HTML")
    parser.add_argument("--section-bin-mm", type=float, default=20.0, help="Axial bin width for circle fitting")
    parser.add_argument("--min-section-points", type=int, default=80, help="Minimum points per section")
    parser.add_argument("--trim-sigma", type=float, default=3.0, help="Robust circle residual trim threshold")
    parser.add_argument("--robust-iterations", type=int, default=3)
    parser.add_argument("--reference-radius-mm", type=float, default=None, help="Radius reference. Default: median fitted radius")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_csv = Path(args.output_csv) if args.output_csv else default_output(input_path, "_axis_corrected.csv")
    output_centers = Path(args.output_centers) if args.output_centers else default_output(input_path, "_axis_centers.csv")
    output_html = Path(args.output_html) if args.output_html else default_output(input_path, "_axis_corrected.html")

    rows = load_points(input_path)
    xyz = np.array([[float(r["x_mm"]), float(r["y_mm"]), float(r["z_mm"])] for r in rows], dtype=float)
    centers = estimate_section_centers(
        xyz,
        bin_mm=args.section_bin_mm,
        min_points=args.min_section_points,
        trim_sigma=args.trim_sigma,
        robust_iterations=args.robust_iterations,
    )
    axis_origin, axis_direction = fit_axis_line(centers)
    corrected, radius_ref = correct_points(rows, axis_origin, axis_direction, args.reference_radius_mm)

    write_csv(corrected, output_csv)
    write_centers_csv(centers, output_centers)
    write_html(corrected, centers, output_html, f"Axis Corrected Point Cloud: {input_path.name}")

    print(f"Input points: {len(rows)}")
    print(f"Section centers: {len(centers)}")
    print(
        "Axis origin mm: "
        f"x={axis_origin[0]:.3f}, y={axis_origin[1]:.3f}, z={axis_origin[2]:.3f}"
    )
    print(
        "Axis direction: "
        f"x={axis_direction[0]:.6f}, y={axis_direction[1]:.6f}, z={axis_direction[2]:.6f}"
    )
    print(f"Reference radius mm: {radius_ref:.3f}")
    print(f"Wrote corrected CSV: {output_csv}")
    print(f"Wrote centers CSV: {output_centers}")
    print(f"Wrote HTML: {output_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
