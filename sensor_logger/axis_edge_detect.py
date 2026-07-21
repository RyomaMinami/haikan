"""
Detect dent edges from an axis-corrected pipe point cloud.

This is tuned for the LK-G85A setup used here:

* The LK-G85A laser band is parallel to the pipe axis.
* Axial changes are relatively reliable.
* Circumferential / rotation-direction changes are weaker and noisier.

So the detector uses anisotropic processing:

1. Grid the point cloud by fitted pipe-axis position and angle.
2. Smooth more strongly in the angle direction than in the axis direction.
3. Calculate edge strength with axial gradient dominant and angular gradient
   down-weighted.

Input:
    auto_scan_..._point_cloud_axis_corrected.csv

Outputs:
    *_axis_edges.csv
    *_axis_edge_grid.csv
    *_axis_edges.html
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.ndimage import gaussian_filter


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


def fill_nan_grid(grid: np.ndarray) -> np.ndarray:
    """Fill NaNs by angle-wise interpolation, then by nearest global value."""
    filled = grid.copy()
    n_angle, n_axis = filled.shape
    x = np.arange(n_axis)

    for i in range(n_angle):
        row = filled[i]
        ok = np.isfinite(row)
        if np.count_nonzero(ok) >= 2:
            row[~ok] = np.interp(x[~ok], x[ok], row[ok])
        elif np.count_nonzero(ok) == 1:
            row[~ok] = row[ok][0]
        filled[i] = row

    ok_all = np.isfinite(filled)
    if not np.all(ok_all):
        if np.any(ok_all):
            median = float(np.nanmedian(filled))
        else:
            median = 0.0
        filled[~ok_all] = median
    return filled


def build_grid(
    df: pd.DataFrame,
    axis_step_mm: float,
    value_column: str,
    angle_column: str,
    axis_column: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    df = df[[axis_column, angle_column, value_column]].dropna().copy()
    df[axis_column] = df[axis_column].astype(float)
    df[angle_column] = df[angle_column].astype(float)
    df[value_column] = df[value_column].astype(float)

    # Use the commanded/measured angle groups as rows. This preserves the
    # actual scan topology better than forcing a dense angular interpolation.
    angles = np.array(sorted(df[angle_column].round(6).unique()), dtype=float)
    s_min = math.floor(float(df[axis_column].min()) / axis_step_mm) * axis_step_mm
    s_max = math.ceil(float(df[axis_column].max()) / axis_step_mm) * axis_step_mm
    axes = np.arange(s_min, s_max + axis_step_mm * 0.5, axis_step_mm, dtype=float)

    grid = np.full((len(angles), len(axes)), np.nan, dtype=float)
    counts = np.zeros_like(grid)
    angle_index = {a: i for i, a in enumerate(angles)}

    for angle, group in df.groupby(df[angle_column].round(6)):
        i = angle_index[float(angle)]
        s = group[axis_column].to_numpy(dtype=float)
        v = group[value_column].to_numpy(dtype=float)
        order = np.argsort(s)
        s = s[order]
        v = v[order]

        # Deduplicate near-identical positions by averaging.
        tmp = pd.DataFrame({"s": s, "v": v})
        tmp["s_key"] = np.round(tmp["s"] / axis_step_mm).astype(int)
        tmp = tmp.groupby("s_key", as_index=False).agg({"s": "mean", "v": "mean"})
        if len(tmp) >= 2:
            valid = (axes >= float(tmp["s"].min())) & (axes <= float(tmp["s"].max()))
            grid[i, valid] = np.interp(axes[valid], tmp["s"].to_numpy(), tmp["v"].to_numpy())
            counts[i, valid] = 1

    return angles, axes, grid, counts


def edge_detect(
    grid: np.ndarray,
    axes: np.ndarray,
    angles: np.ndarray,
    radius_mm: float,
    sigma_axis_mm: float,
    sigma_angle_steps: float,
    theta_gradient_weight: float,
    edge_percentile: float,
    outward_gate_percentile: Optional[float],
) -> dict[str, np.ndarray | float]:
    axis_step = float(np.median(np.diff(axes))) if len(axes) > 1 else 1.0
    angle_step_deg = float(np.median(np.diff(angles))) if len(angles) > 1 else 5.0
    arc_step = max(radius_mm * math.radians(abs(angle_step_deg)), 1e-9)

    filled = fill_nan_grid(grid)
    sigma_axis_steps = max(sigma_axis_mm / max(axis_step, 1e-9), 0.0)
    smoothed = gaussian_filter(
        filled,
        sigma=(sigma_angle_steps, sigma_axis_steps),
        mode=("nearest", "nearest"),
    )

    grad_angle, grad_axis = np.gradient(smoothed, arc_step, axis_step)
    edge_score = np.sqrt(grad_axis * grad_axis + theta_gradient_weight * grad_angle * grad_angle)

    threshold = float(np.nanpercentile(edge_score, edge_percentile))
    edge_mask = edge_score >= threshold
    if outward_gate_percentile is not None:
        gate = float(np.nanpercentile(smoothed, outward_gate_percentile))
        edge_mask &= smoothed >= gate
    else:
        gate = float("nan")

    return {
        "filled": filled,
        "smoothed": smoothed,
        "grad_axis": grad_axis,
        "grad_angle": grad_angle,
        "edge_score": edge_score,
        "edge_mask": edge_mask,
        "threshold": threshold,
        "gate": gate,
    }


def write_grid_csv(
    path: Path,
    angles: np.ndarray,
    axes: np.ndarray,
    grid: np.ndarray,
    result: dict[str, np.ndarray | float],
) -> None:
    smoothed = result["smoothed"]
    edge_score = result["edge_score"]
    edge_mask = result["edge_mask"]
    assert isinstance(smoothed, np.ndarray)
    assert isinstance(edge_score, np.ndarray)
    assert isinstance(edge_mask, np.ndarray)

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "axis_s_mm",
                "angle_deg",
                "outward_mm",
                "smoothed_outward_mm",
                "edge_score",
                "is_edge",
            ],
        )
        writer.writeheader()
        for i, angle in enumerate(angles):
            for j, axis in enumerate(axes):
                writer.writerow(
                    {
                        "axis_s_mm": f"{axis:.3f}",
                        "angle_deg": f"{angle:.6g}",
                        "outward_mm": "" if not np.isfinite(grid[i, j]) else f"{grid[i, j]:.6f}",
                        "smoothed_outward_mm": f"{smoothed[i, j]:.6f}",
                        "edge_score": f"{edge_score[i, j]:.6f}",
                        "is_edge": int(bool(edge_mask[i, j])),
                    }
                )


def write_edge_csv(
    path: Path,
    angles: np.ndarray,
    axes: np.ndarray,
    result: dict[str, np.ndarray | float],
    radius_mm: float,
) -> int:
    smoothed = result["smoothed"]
    edge_score = result["edge_score"]
    edge_mask = result["edge_mask"]
    assert isinstance(smoothed, np.ndarray)
    assert isinstance(edge_score, np.ndarray)
    assert isinstance(edge_mask, np.ndarray)

    count = 0
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "axis_s_mm",
                "angle_deg",
                "edge_y_mm",
                "edge_z_mm",
                "smoothed_outward_mm",
                "edge_score",
            ],
        )
        writer.writeheader()
        for i, angle in enumerate(angles):
            theta = math.radians(float(angle))
            for j, axis in enumerate(axes):
                if not edge_mask[i, j]:
                    continue
                r = radius_mm + float(smoothed[i, j])
                writer.writerow(
                    {
                        "axis_s_mm": f"{axis:.3f}",
                        "angle_deg": f"{angle:.6g}",
                        "edge_y_mm": f"{r * math.cos(theta):.6f}",
                        "edge_z_mm": f"{r * math.sin(theta):.6f}",
                        "smoothed_outward_mm": f"{smoothed[i, j]:.6f}",
                        "edge_score": f"{edge_score[i, j]:.6f}",
                    }
                )
                count += 1
    return count


def matrix_for_plot(arr: np.ndarray) -> list[list[Optional[float]]]:
    out: list[list[Optional[float]]] = []
    for row in arr:
        out.append([None if not np.isfinite(v) else float(v) for v in row])
    return out


def write_html(
    path: Path,
    title: str,
    angles: np.ndarray,
    axes: np.ndarray,
    raw_grid: np.ndarray,
    result: dict[str, np.ndarray | float],
    edge_csv_path: Path,
    radius_mm: float,
) -> None:
    smoothed = result["smoothed"]
    edge_score = result["edge_score"]
    edge_mask = result["edge_mask"]
    threshold = float(result["threshold"])
    gate = float(result["gate"])
    assert isinstance(smoothed, np.ndarray)
    assert isinstance(edge_score, np.ndarray)
    assert isinstance(edge_mask, np.ndarray)

    edge_x: list[float] = []
    edge_y: list[float] = []
    edge_z: list[float] = []
    edge_c: list[float] = []
    axis_min = float(np.min(axes))
    axes_zero = axes - axis_min
    for i, angle in enumerate(angles):
        theta = math.radians(float(angle))
        for j, axis in enumerate(axes):
            if edge_mask[i, j]:
                r = radius_mm + float(smoothed[i, j])
                edge_x.append(float(axis - axis_min))
                edge_y.append(r * math.cos(theta))
                edge_z.append(r * math.sin(theta))
                edge_c.append(float(edge_score[i, j]))

    payload = {
        "axes": [float(x) for x in axes_zero],
        "angles": [float(a) for a in angles],
        "raw": matrix_for_plot(raw_grid),
        "smooth": matrix_for_plot(smoothed),
        "edge": matrix_for_plot(edge_score),
        "edgeMask": matrix_for_plot(np.where(edge_mask, edge_score, np.nan)),
        "edgeX": edge_x,
        "edgeY": edge_y,
        "edgeZ": edge_z,
        "edgeC": edge_c,
        "threshold": threshold,
        "gate": gate,
        "edgeCsv": edge_csv_path.name,
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
    p {{ margin: 0; font-size: 13px; color: #555; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px; }}
    .plot {{ min-height: 440px; background: #fff; border: 1px solid #ddd; }}
    .wide {{ grid-column: 1 / -1; min-height: 560px; }}
    @media (max-width: 980px) {{ .grid {{ grid-template-columns: 1fr; }} .wide {{ grid-column: auto; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{title}</h1>
    <p>LK-G85Aの軸方向優位性を考慮し、回転方向を強めに平滑化し、回転方向勾配を低く重み付けしたエッジ検出です。</p>
  </header>
  <div class="grid">
    <div id="raw" class="plot"></div>
    <div id="smooth" class="plot"></div>
    <div id="score" class="plot"></div>
    <div id="mask" class="plot"></div>
    <div id="edges3d" class="plot wide"></div>
  </div>
  <script>
    const d = {json.dumps(payload)};
    const heatLayout = (name) => ({{
      title: name,
      xaxis: {{title: 'x mm'}},
      yaxis: {{title: 'angle deg'}},
      margin: {{l:64,r:20,t:42,b:48}}
    }});
    Plotly.newPlot('raw', [{{
      type: 'heatmap', x: d.axes, y: d.angles, z: d.raw,
      colorscale: 'RdBu', reversescale: true, colorbar: {{title: 'out mm'}}
    }}], heatLayout('Raw outward map'), {{responsive:true}});
    Plotly.newPlot('smooth', [{{
      type: 'heatmap', x: d.axes, y: d.angles, z: d.smooth,
      colorscale: 'RdBu', reversescale: true, colorbar: {{title: 'out mm'}}
    }}], heatLayout('Anisotropic smoothed map'), {{responsive:true}});
    Plotly.newPlot('score', [{{
      type: 'heatmap', x: d.axes, y: d.angles, z: d.edge,
      colorscale: 'Viridis', colorbar: {{title: 'score'}}
    }}], heatLayout(`Edge score, threshold=${{d.threshold.toFixed(4)}}`), {{responsive:true}});
    Plotly.newPlot('mask', [{{
      type: 'heatmap', x: d.axes, y: d.angles, z: d.edgeMask,
      colorscale: 'Hot', colorbar: {{title: 'score'}}
    }}], heatLayout('Detected edge candidates'), {{responsive:true}});
    Plotly.newPlot('edges3d', [{{
      type: 'scatter3d', mode: 'markers',
      x: d.edgeX, y: d.edgeY, z: d.edgeZ,
      marker: {{size: 3, color: d.edgeC, colorscale: 'Hot', colorbar: {{title: 'score'}}}},
      name: 'edges'
    }}], {{
      title: 'Edge points in corrected pipe-axis coordinates',
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
    if stem.endswith("_point_cloud_axis_corrected"):
        stem = stem[: -len("_point_cloud_axis_corrected")]
    return input_path.with_name(f"{stem}{suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Anisotropic LK-G85A-aware dent edge detection.")
    parser.add_argument("--input", required=True, help="Axis-corrected point-cloud CSV")
    parser.add_argument("--output-edges", help="Output edge point CSV")
    parser.add_argument("--output-grid", help="Output gridded edge-score CSV")
    parser.add_argument("--output-html", help="Output visualization HTML")
    parser.add_argument("--value-column", default="fitted_outward_mm")
    parser.add_argument("--axis-column", default="axis_s_mm")
    parser.add_argument("--angle-column", default="angle_deg", help="Use angle_deg for scan topology, or fitted_theta_deg")
    parser.add_argument("--radius-mm", type=float, default=120.0)
    parser.add_argument("--axis-step-mm", type=float, default=1.0)
    parser.add_argument("--sigma-axis-mm", type=float, default=0.8, help="Small smoothing to preserve axial edges")
    parser.add_argument("--sigma-angle-steps", type=float, default=1.6, help="Larger smoothing because rotation direction is weaker")
    parser.add_argument("--theta-gradient-weight", type=float, default=0.25, help="Down-weight angular gradient")
    parser.add_argument("--edge-percentile", type=float, default=95.0)
    parser.add_argument(
        "--outward-gate-percentile",
        type=float,
        default=45.0,
        help="Keep edges near outward-positive areas. Use none by passing a negative value.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_edges = Path(args.output_edges) if args.output_edges else default_path(input_path, "_axis_edges.csv")
    output_grid = Path(args.output_grid) if args.output_grid else default_path(input_path, "_axis_edge_grid.csv")
    output_html = Path(args.output_html) if args.output_html else default_path(input_path, "_axis_edges.html")

    df = pd.read_csv(input_path, encoding="utf-8-sig")
    for column in [args.axis_column, args.angle_column, args.value_column]:
        if column not in df.columns:
            raise SystemExit(f"Column not found: {column}")

    angles, axes, grid, _ = build_grid(
        df,
        axis_step_mm=args.axis_step_mm,
        value_column=args.value_column,
        angle_column=args.angle_column,
        axis_column=args.axis_column,
    )
    gate = args.outward_gate_percentile if args.outward_gate_percentile >= 0 else None
    result = edge_detect(
        grid,
        axes=axes,
        angles=angles,
        radius_mm=args.radius_mm,
        sigma_axis_mm=args.sigma_axis_mm,
        sigma_angle_steps=args.sigma_angle_steps,
        theta_gradient_weight=args.theta_gradient_weight,
        edge_percentile=args.edge_percentile,
        outward_gate_percentile=gate,
    )

    write_grid_csv(output_grid, angles, axes, grid, result)
    edge_count = write_edge_csv(output_edges, angles, axes, result, args.radius_mm)
    write_html(output_html, f"Axis Edge Detection: {input_path.name}", angles, axes, grid, result, output_edges, args.radius_mm)

    print(f"Input rows: {len(df)}")
    print(f"Grid: angles={len(angles)}, axis_points={len(axes)}, axis_step={args.axis_step_mm} mm")
    print(f"Edge threshold: {float(result['threshold']):.6f}")
    if gate is not None:
        print(f"Outward gate percentile: {gate:.1f}")
    print(f"Edge points: {edge_count}")
    print(f"Wrote edge CSV: {output_edges}")
    print(f"Wrote grid CSV: {output_grid}")
    print(f"Wrote HTML: {output_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
