"""
Match an expected circular dent edge against LK-G85A-aware edge scores.

Use this after axis_edge_detect.py.

The dent is circular on the unwrapped pipe surface:
    horizontal: pipe-axis position [mm]
    vertical  : circumferential distance = pipe_radius * angle_rad [mm]

LK-G85A characteristic used here:
    The laser band is parallel to the pipe axis, so axial edge response is
    considered more trustworthy than circumferential edge response.

The expected circular edge template therefore emphasizes the left/right parts
of the circle, where the edge normal has a strong pipe-axis component, and
down-weights the top/bottom parts, where the response depends more on rotation
direction.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import fftconvolve


def load_edge_grid(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    df = pd.read_csv(path, encoding="utf-8-sig")
    required = {"axis_s_mm", "angle_deg", "edge_score", "smoothed_outward_mm"}
    missing = required - set(df.columns)
    if missing:
        raise SystemExit(f"Missing columns in edge grid: {', '.join(sorted(missing))}")

    axes = np.array(sorted(df["axis_s_mm"].astype(float).unique()), dtype=float)
    angles = np.array(sorted(df["angle_deg"].astype(float).unique()), dtype=float)
    score = np.zeros((len(angles), len(axes)), dtype=float)
    outward = np.zeros_like(score)
    ai = {round(a, 6): i for i, a in enumerate(angles)}
    xi = {round(x, 6): j for j, x in enumerate(axes)}
    for row in df.itertuples(index=False):
        i = ai[round(float(row.angle_deg), 6)]
        j = xi[round(float(row.axis_s_mm), 6)]
        score[i, j] = float(row.edge_score)
        outward[i, j] = float(row.smoothed_outward_mm)
    return axes, angles, score, outward


def robust_normalize_positive(values: np.ndarray) -> np.ndarray:
    med = float(np.median(values))
    mad = float(np.median(np.abs(values - med)))
    scale = 1.4826 * mad if mad > 1e-9 else float(np.std(values))
    if scale <= 1e-9:
        scale = 1.0
    z = (values - med) / scale
    return np.clip(z, 0.0, None)


def make_circle_edge_kernel(
    hole_radius_mm: float,
    axis_step_mm: float,
    angle_step_deg: float,
    pipe_radius_mm: float,
    edge_band_mm: float,
    angular_edge_weight: float,
    kernel_margin_mm: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    circ_step_mm = max(pipe_radius_mm * math.radians(abs(angle_step_deg)), 1e-9)
    half_axis = int(math.ceil((hole_radius_mm + kernel_margin_mm) / axis_step_mm))
    half_angle = int(math.ceil((hole_radius_mm + kernel_margin_mm) / circ_step_mm))
    dx = np.arange(-half_axis, half_axis + 1, dtype=float) * axis_step_mm
    dc = np.arange(-half_angle, half_angle + 1, dtype=float) * circ_step_mm
    yy, xx = np.meshgrid(dc, dx, indexing="ij")
    dist = np.sqrt(xx * xx + yy * yy)
    ring = np.exp(-0.5 * ((dist - hole_radius_mm) / max(edge_band_mm, 1e-6)) ** 2)

    # Edge normal component. x-dominant edges are most reliable for this sensor.
    normal_x = np.abs(xx) / np.maximum(dist, 1e-9)
    normal_c = np.abs(yy) / np.maximum(dist, 1e-9)
    visibility = np.sqrt(normal_x * normal_x + angular_edge_weight * normal_c * normal_c)
    kernel = ring * visibility
    kernel[dist < hole_radius_mm - 3.0 * edge_band_mm] = 0.0
    kernel[dist > hole_radius_mm + 3.0 * edge_band_mm] = 0.0
    kernel -= float(np.mean(kernel))
    norm = float(np.sqrt(np.sum(kernel * kernel)))
    if norm > 1e-12:
        kernel /= norm
    return kernel, dx, dc


def normalized_template_score(score: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    measured = robust_normalize_positive(score)
    local_energy = np.sqrt(fftconvolve(measured * measured, np.ones_like(kernel), mode="same"))
    response = fftconvolve(measured, kernel[::-1, ::-1], mode="same")
    return np.divide(response, local_energy, out=np.zeros_like(response), where=local_energy > 1e-9)


def best_match(
    axes: np.ndarray,
    angles: np.ndarray,
    score_map: np.ndarray,
    outward: np.ndarray,
    kernel: np.ndarray,
) -> dict[str, float | int]:
    template_score = normalized_template_score(score_map, kernel)
    # Prefer outward-positive dent areas very lightly, without hard thresholding.
    outward_z = robust_normalize_positive(outward)
    combined = template_score + 0.04 * outward_z
    idx = np.unravel_index(int(np.argmax(combined)), combined.shape)
    i, j = int(idx[0]), int(idx[1])
    return {
        "angle_index": i,
        "axis_index": j,
        "center_axis_s_mm": float(axes[j]),
        "center_angle_deg": float(angles[i]),
        "template_score": float(template_score[i, j]),
        "combined_score": float(combined[i, j]),
        "center_outward_mm": float(outward[i, j]),
    }


def boundary_points(
    center_axis: float,
    center_angle: float,
    hole_radius_mm: float,
    pipe_radius_mm: float,
    base_radius_mm: float,
    n: int = 240,
) -> list[dict[str, float]]:
    rows = []
    center_circ = pipe_radius_mm * math.radians(center_angle)
    for k in range(n):
        t = 2.0 * math.pi * k / n
        axis = center_axis + hole_radius_mm * math.cos(t)
        circ = center_circ + hole_radius_mm * math.sin(t)
        angle = math.degrees(circ / pipe_radius_mm)
        theta = math.radians(angle)
        rows.append(
            {
                "boundary_index": k,
                "axis_s_mm": axis,
                "angle_deg": angle,
                "circum_mm": circ,
                "x_mm": axis,
                "y_mm": base_radius_mm * math.cos(theta),
                "z_mm": base_radius_mm * math.sin(theta),
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, float | int]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_summary(path: Path, row: dict[str, float | int | str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)


def matrix_plot(arr: np.ndarray) -> list[list[float]]:
    return [[float(v) for v in row] for row in arr]


def write_html(
    path: Path,
    title: str,
    axes: np.ndarray,
    angles: np.ndarray,
    edge_score: np.ndarray,
    template_score: np.ndarray,
    outward: np.ndarray,
    boundary: list[dict[str, float]],
    best: dict[str, float | int],
    hole_diameter_mm: float,
) -> None:
    axis_min = float(np.min(axes))
    axes_zero = axes - axis_min
    best_for_plot = dict(best)
    best_for_plot["center_x_mm"] = float(best["center_axis_s_mm"]) - axis_min
    payload = {
        "axes": [float(x) for x in axes_zero],
        "angles": [float(a) for a in angles],
        "edge": matrix_plot(edge_score),
        "templateScore": matrix_plot(template_score),
        "outward": matrix_plot(outward),
        "boundaryX": [float(r["axis_s_mm"]) - axis_min for r in boundary],
        "boundaryAngle": [float(r["angle_deg"]) for r in boundary],
        "boundaryY": [float(r["y_mm"]) for r in boundary],
        "boundaryZ": [float(r["z_mm"]) for r in boundary],
        "best": best_for_plot,
        "holeDiameter": hole_diameter_mm,
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
    p {{ margin: 0; color: #555; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px; }}
    .plot {{ min-height: 460px; background: #fff; border: 1px solid #ddd; }}
    .wide {{ grid-column: 1 / -1; min-height: 560px; }}
    @media (max-width: 980px) {{ .grid {{ grid-template-columns: 1fr; }} .wide {{ grid-column: auto; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p>直径{hole_diameter_mm:g}mmの円形エッジテンプレートとLK-G85A軸方向優位のエッジスコアを比較した結果です。</p>
  </header>
  <div class="grid">
    <div id="edge" class="plot"></div>
    <div id="match" class="plot"></div>
    <div id="overlay" class="plot wide"></div>
    <div id="boundary3d" class="plot wide"></div>
  </div>
  <script>
    const d = {json.dumps(payload)};
    const heatLayout = (title) => ({{
      title,
      xaxis: {{title: 'x mm'}},
      yaxis: {{title: 'angle deg'}},
      margin: {{l:64,r:20,t:42,b:48}}
    }});
    Plotly.newPlot('edge', [{{
      type: 'heatmap', x: d.axes, y: d.angles, z: d.edge,
      colorscale: 'Viridis', colorbar: {{title: 'edge'}}
    }}], heatLayout('LK-G85A-aware edge score'), {{responsive:true}});
    Plotly.newPlot('match', [{{
      type: 'heatmap', x: d.axes, y: d.angles, z: d.templateScore,
      colorscale: 'Turbo', colorbar: {{title: 'match'}}
    }}, {{
      type: 'scatter', mode: 'markers',
      x: [d.best.center_x_mm], y: [d.best.center_angle_deg],
      marker: {{size: 13, color: '#111', symbol: 'x'}},
      name: 'best center'
    }}], heatLayout(`Template match score: best=${{d.best.template_score.toFixed(4)}}`), {{responsive:true}});
    Plotly.newPlot('overlay', [{{
      type: 'heatmap', x: d.axes, y: d.angles, z: d.edge,
      colorscale: 'Greys', colorbar: {{title: 'edge'}}
    }}, {{
      type: 'scatter', mode: 'lines',
      x: d.boundaryX, y: d.boundaryAngle,
      line: {{color: '#ff2d2d', width: 3}},
      name: 'matched circular edge'
    }}, {{
      type: 'scatter', mode: 'markers',
      x: [d.best.center_x_mm], y: [d.best.center_angle_deg],
      marker: {{size: 10, color: '#008cff'}},
      name: 'center'
    }}], heatLayout('Detected circular edge overlay'), {{responsive:true}});
    Plotly.newPlot('boundary3d', [{{
      type: 'scatter3d', mode: 'lines',
      x: d.boundaryX, y: d.boundaryY, z: d.boundaryZ,
      line: {{color: '#ff2d2d', width: 6}},
      name: 'matched circular edge'
    }}], {{
      title: 'Detected circular edge in pipe coordinates',
      scene: {{
      xaxis: {{title: 'x mm'}},
        yaxis: {{title: 'axis y mm'}},
        zaxis: {{title: 'axis z mm'}},
        aspectmode: 'data'
      }},
      margin: {{l:0,r:0,t:42,b:0}}
    }}, {{responsive:true}});
  </script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def default_path(input_path: Path, suffix: str) -> Path:
    stem = input_path.stem
    if stem.endswith("_axis_edge_grid") or stem.endswith("_axis_edge_grid_p97"):
        stem = stem.replace("_axis_edge_grid_p97", "").replace("_axis_edge_grid", "")
    return input_path.with_name(f"{stem}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate circular dent edge template against edge-score map.")
    parser.add_argument("--input-grid", required=True, help="CSV from axis_edge_detect.py")
    parser.add_argument("--hole-diameter-mm", type=float, default=199.0)
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--base-radius-mm", type=float, default=120.0)
    parser.add_argument("--edge-band-mm", type=float, default=5.0)
    parser.add_argument("--angular-edge-weight", type=float, default=0.25)
    parser.add_argument("--kernel-margin-mm", type=float, default=18.0)
    parser.add_argument("--output-summary")
    parser.add_argument("--output-boundary")
    parser.add_argument("--output-html")
    args = parser.parse_args()

    input_path = Path(args.input_grid)
    summary_path = Path(args.output_summary) if args.output_summary else default_path(input_path, "_circular_edge_match_summary.csv")
    boundary_path = Path(args.output_boundary) if args.output_boundary else default_path(input_path, "_circular_edge_boundary.csv")
    html_path = Path(args.output_html) if args.output_html else default_path(input_path, "_circular_edge_match.html")

    axes, angles, edge_score, outward = load_edge_grid(input_path)
    axis_step = float(np.median(np.diff(axes))) if len(axes) > 1 else 1.0
    angle_step = float(np.median(np.diff(angles))) if len(angles) > 1 else 5.0
    hole_radius = args.hole_diameter_mm / 2.0
    kernel, _, _ = make_circle_edge_kernel(
        hole_radius,
        axis_step_mm=axis_step,
        angle_step_deg=angle_step,
        pipe_radius_mm=args.pipe_radius_mm,
        edge_band_mm=args.edge_band_mm,
        angular_edge_weight=args.angular_edge_weight,
        kernel_margin_mm=args.kernel_margin_mm,
    )
    template_score = normalized_template_score(edge_score, kernel)
    best = best_match(axes, angles, edge_score, outward, kernel)
    boundary = boundary_points(
        float(best["center_axis_s_mm"]),
        float(best["center_angle_deg"]),
        hole_radius,
        args.pipe_radius_mm,
        args.base_radius_mm,
    )
    angle_span = math.degrees(args.hole_diameter_mm / args.pipe_radius_mm)
    summary = {
        "input_grid": input_path.name,
        "hole_diameter_mm": args.hole_diameter_mm,
        "hole_radius_mm": hole_radius,
        "pipe_radius_mm": args.pipe_radius_mm,
        "center_axis_s_mm": best["center_axis_s_mm"],
        "center_angle_deg": best["center_angle_deg"],
        "template_score": best["template_score"],
        "combined_score": best["combined_score"],
        "center_outward_mm": best["center_outward_mm"],
        "estimated_angle_span_deg": angle_span,
        "edge_band_mm": args.edge_band_mm,
        "angular_edge_weight": args.angular_edge_weight,
        "kernel_rows": kernel.shape[0],
        "kernel_cols": kernel.shape[1],
    }
    write_summary(summary_path, summary)
    write_csv(boundary_path, boundary)
    write_html(
        html_path,
        f"Circular Edge Template Match: {input_path.name}",
        axes,
        angles,
        edge_score,
        template_score,
        outward,
        boundary,
        best,
        args.hole_diameter_mm,
    )

    print(f"Input grid: {input_path}")
    print(f"Hole diameter: {args.hole_diameter_mm:.3f} mm")
    print(f"Best center axis_s: {float(best['center_axis_s_mm']):.3f} mm")
    print(f"Best center angle: {float(best['center_angle_deg']):.3f} deg")
    print(f"Template score: {float(best['template_score']):.6f}")
    print(f"Combined score: {float(best['combined_score']):.6f}")
    print(f"Estimated angle span: {angle_span:.3f} deg")
    print(f"Wrote summary: {summary_path}")
    print(f"Wrote boundary: {boundary_path}")
    print(f"Wrote HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
