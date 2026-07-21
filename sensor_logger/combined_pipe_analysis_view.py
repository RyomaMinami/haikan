"""
Create one HTML dashboard for one pipe measurement experiment.

This script intentionally keeps each experiment separate. Pass the CSV/HTML
inputs for one dataset only, for example the 199 mm dataset or the 154 mm
dataset. Optional result-photo information can be added at the end.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Optional

import numpy as np

from axis_edge_template_match import make_circle_edge_kernel, normalized_template_score


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


def read_points(path: Path, x_col: str, y_col: str, z_col: str, color_col: str) -> dict[str, list[float]]:
    data = {"x": [], "y": [], "z": [], "c": []}
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            x = parse_float(row.get(x_col))
            y = parse_float(row.get(y_col))
            z = parse_float(row.get(z_col))
            c = parse_float(row.get(color_col))
            if x is None or y is None or z is None or c is None:
                continue
            data["x"].append(x)
            data["y"].append(y)
            data["z"].append(z)
            data["c"].append(c)
    return data


def read_edge_grid(path: Path) -> dict[str, object]:
    rows: list[tuple[float, float, float, float]] = []
    axes_set: set[float] = set()
    angles_set: set[float] = set()
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            axis = parse_float(row.get("axis_s_mm"))
            angle = parse_float(row.get("angle_deg"))
            edge = parse_float(row.get("edge_score"))
            outward = parse_float(row.get("smoothed_outward_mm"))
            if axis is None or angle is None or edge is None or outward is None:
                continue
            rows.append((axis, angle, edge, outward))
            axes_set.add(axis)
            angles_set.add(angle)

    axes = sorted(axes_set)
    angles = sorted(angles_set)
    if not axes or not angles:
        raise SystemExit(f"No usable edge grid data: {path}")

    axis_offset = min(axes)
    axes_zero = [x - axis_offset for x in axes]
    ai = {a: i for i, a in enumerate(angles)}
    xi = {x: j for j, x in enumerate(axes)}
    edge_grid: list[list[Optional[float]]] = [[None for _ in axes] for _ in angles]
    outward_grid: list[list[Optional[float]]] = [[None for _ in axes] for _ in angles]
    for axis, angle, edge, outward in rows:
        edge_grid[ai[angle]][xi[axis]] = edge
        outward_grid[ai[angle]][xi[axis]] = outward

    return {
        "axes": axes_zero,
        "axisOffset": axis_offset,
        "angles": angles,
        "edge": edge_grid,
        "outward": outward_grid,
    }


def read_boundary(path: Path, axis_offset: float) -> dict[str, list[float]]:
    data = {"x": [], "angle": [], "y": [], "z": []}
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            axis = parse_float(row.get("axis_s_mm"))
            angle = parse_float(row.get("angle_deg"))
            y = parse_float(row.get("y_mm"))
            z = parse_float(row.get("z_mm"))
            if axis is None or angle is None or y is None or z is None:
                continue
            data["x"].append(axis - axis_offset)
            data["angle"].append(angle)
            data["y"].append(y)
            data["z"].append(z)
    return data


def read_dent_region(path: Path) -> dict[str, dict[str, list[float]]]:
    groups = {
        "outside": {"x": [], "y": [], "z": [], "c": []},
        "inside": {"x": [], "y": [], "z": [], "c": []},
        "edge": {"x": [], "y": [], "z": [], "c": []},
    }
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            x = parse_float(row.get("x_zero_mm"))
            y = parse_float(row.get("axis_y_mm"))
            z = parse_float(row.get("axis_z_mm"))
            c = parse_float(row.get("fitted_outward_mm"))
            if x is None or y is None or z is None or c is None:
                continue
            region = row.get("dent_region", "outside")
            key = "edge" if region == "edge" else ("inside" if region == "inside" else "outside")
            groups[key]["x"].append(x)
            groups[key]["y"].append(y)
            groups[key]["z"].append(z)
            groups[key]["c"].append(c)
    return groups


def read_summary(path: Path) -> dict[str, object]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}
    out: dict[str, object] = {}
    for key, value in rows[0].items():
        parsed = parse_float(value)
        out[key] = parsed if parsed is not None else value
    return out


def make_template_score(edge_grid: dict[str, object], summary: dict[str, object]) -> list[list[float]]:
    edge_array = np.array(edge_grid["edge"], dtype=float)
    axes = np.array(edge_grid["axes"], dtype=float)
    angles = np.array(edge_grid["angles"], dtype=float)
    axis_step = float(np.median(np.diff(axes))) if len(axes) > 1 else 1.0
    angle_step = float(np.median(np.diff(angles))) if len(angles) > 1 else 5.0
    hole_radius = float(summary.get("hole_radius_mm", 77.0))
    pipe_radius = float(summary.get("pipe_radius_mm", 120.0))
    kernel, _, _ = make_circle_edge_kernel(
        hole_radius,
        axis_step_mm=axis_step,
        angle_step_deg=angle_step,
        pipe_radius_mm=pipe_radius,
        edge_band_mm=5.0,
        angular_edge_weight=0.25,
        kernel_margin_mm=18.0,
    )
    score = normalized_template_score(edge_array, kernel)
    return [[float(v) for v in row] for row in score]


def write_html(payload: dict[str, object], output: Path) -> None:
    result = payload.get("result", {})
    result_section = ""
    if isinstance(result, dict) and result.get("image"):
        result_section = f"""
    <section class="wide">
      <h2>8. 実機照射確認結果</h2>
      <p>{result.get("description", "推定中心へレーザーを照射した確認写真です。")}</p>
      <div class="result-wrap">
        <img class="result-img" src="{result["image"]}" alt="Laser result">
        <table class="result-table">
          <tr><th>項目</th><th>結果</th></tr>
          <tr><td>推定窪み中心</td><td>x = {result.get("center_x", "")} mm, angle = {result.get("center_angle", "")} deg</td></tr>
          <tr><td>画像上のレーザー中心</td><td>{result.get("laser_definition", "白い中心コアをレーザー位置として使用")}</td></tr>
          <tr><td>中心からのズレ</td><td>{result.get("offset", "")}</td></tr>
          <tr><td>補正方向</td><td>{result.get("correction", "")}</td></tr>
          <tr><td>注意</td><td>{result.get("note", "写真からの概算のため、カメラ角度やシートのしわで数mm程度の誤差があります。")}</td></tr>
        </table>
      </div>
    </section>
"""

    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{payload["title"]}</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f5f5f2; color: #222; }}
    header {{ padding: 14px 18px; background: #fff; border-bottom: 1px solid #d5d5cf; position: sticky; top: 0; z-index: 5; }}
    h1 {{ font-size: 19px; margin: 0 0 6px; }}
    .meta {{ color: #555; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 12px; }}
    section {{ background: #fff; border: 1px solid #ddd; }}
    section h2 {{ font-size: 16px; margin: 12px 14px 4px; }}
    section p {{ margin: 0 14px 10px; color: #555; font-size: 13px; line-height: 1.45; }}
    .panel {{ min-height: 500px; }}
    .wide {{ grid-column: 1 / -1; }}
    .wide .panel {{ min-height: 620px; }}
    .result-wrap {{ padding: 0 14px 16px; }}
    .result-img {{ width: 100%; max-height: 900px; object-fit: contain; background: #111; display: block; }}
    .result-table {{ margin: 10px 0 0; border-collapse: collapse; font-size: 14px; }}
    .result-table th, .result-table td {{ border: 1px solid #d7d7d2; padding: 6px 10px; text-align: left; }}
    .result-table th {{ background: #f0f0ea; }}
    @media (max-width: 1100px) {{ .grid {{ grid-template-columns: 1fr; }} .wide {{ grid-column: auto; }} }}
  </style>
</head>
<body>
  <header>
    <h1>{payload["title"]}</h1>
    <div class="meta">このHTMLは1つの実験データだけをまとめています。補正後のx軸は左端を0mmにした座標です。</div>
  </header>
  <div class="grid">
    <section>
      <h2>1. 生データ点群</h2>
      <p>サーボ角度をそのまま配管中心まわりの角度として配置した点群です。測定軸のずれや傾きはまだ補正していません。</p>
      <div id="raw3d" class="panel"></div>
    </section>
    <section>
      <h2>2. 軸補正後点群</h2>
      <p>各断面の円中心を推定し、その中心列から配管軸を推定します。点群は推定軸からの距離で再計算しています。</p>
      <div id="axis3d" class="panel"></div>
    </section>
    <section>
      <h2>3. 異方性フィルタ後の外向き量</h2>
      <p>LK-G85Aは軸方向の変化に強く、回転方向の変化に弱い可能性があるため、回転方向を強めに平滑化しています。</p>
      <div id="smoothHeat" class="panel"></div>
    </section>
    <section>
      <h2>4. LK-G85A特性込みエッジスコア</h2>
      <p>軸方向勾配を主に使い、回転方向勾配を低く重み付けしてエッジを評価しています。</p>
      <div id="edgeHeat" class="panel"></div>
    </section>
    <section>
      <h2>5. 円形エッジ評価</h2>
      <p>既知の窪み径の円形エッジテンプレートをエッジスコアと比較し、一致度が最も高い中心位置を推定しています。</p>
      <div id="templateMatch" class="panel"></div>
    </section>
    <section>
      <h2>6. 推定円の重ね合わせ</h2>
      <p>円形エッジ評価で推定した境界を、展開マップ上に重ねています。</p>
      <div id="circleMatch" class="panel"></div>
    </section>
    <section class="wide">
      <h2>7. 窪み評価</h2>
      <p>推定中心と半径を使って、軸補正後点群に窪み内側とエッジ帯のフラグを付けています。</p>
      <div id="dent3d" class="panel"></div>
    </section>
{result_section}
  </div>
  <script>
    const d = {json.dumps(payload, ensure_ascii=False)};
    const colorScale = 'RdBu';
    const sceneCommon = {{
      yaxis: {{title: 'y mm'}},
      zaxis: {{title: 'z mm'}},
      aspectmode: 'data',
      camera: {{eye: {{x: 1.45, y: 1.45, z: 0.95}}, up: {{x: 0, y: 0, z: 1}}}}
    }};
    const heatLayout = title => ({{
      title,
      xaxis: {{title: 'x mm'}},
      yaxis: {{title: 'angle deg'}},
      margin: {{l:64,r:20,t:42,b:48}}
    }});

    Plotly.newPlot('raw3d', [{{
      type: 'scatter3d', mode: 'markers',
      x: d.raw.x, y: d.raw.y, z: d.raw.z,
      marker: {{size: 2, color: d.raw.c, colorscale: colorScale, reversescale: true, colorbar: {{title: 'out mm'}}}},
      name: 'raw'
    }}], {{title: 'Raw point cloud', scene: {{...sceneCommon, xaxis: {{title: 'x mm'}}}}, margin: {{l:0,r:0,t:42,b:0}}}}, {{responsive:true}});

    Plotly.newPlot('axis3d', [{{
      type: 'scatter3d', mode: 'markers',
      x: d.axis.x, y: d.axis.y, z: d.axis.z,
      marker: {{size: 2, color: d.axis.c, colorscale: colorScale, reversescale: true, colorbar: {{title: 'out mm'}}}},
      name: 'axis corrected'
    }}], {{title: 'Axis-corrected point cloud', scene: {{...sceneCommon, xaxis: {{title: 'x mm'}}}}, margin: {{l:0,r:0,t:42,b:0}}}}, {{responsive:true}});

    Plotly.newPlot('smoothHeat', [{{
      type: 'heatmap', x: d.edgeGrid.axes, y: d.edgeGrid.angles, z: d.edgeGrid.outward,
      colorscale: 'RdBu', reversescale: true, colorbar: {{title: 'out mm'}}
    }}], heatLayout('Anisotropic smoothed outward map'), {{responsive:true}});

    Plotly.newPlot('edgeHeat', [{{
      type: 'heatmap', x: d.edgeGrid.axes, y: d.edgeGrid.angles, z: d.edgeGrid.edge,
      colorscale: 'Viridis', colorbar: {{title: 'edge'}}
    }}], heatLayout('LK-G85A-aware edge score'), {{responsive:true}});

    Plotly.newPlot('templateMatch', [{{
      type: 'heatmap', x: d.edgeGrid.axes, y: d.edgeGrid.angles, z: d.templateScore,
      colorscale: 'Turbo', colorbar: {{title: 'match'}}
    }}, {{
      type: 'scatter', mode: 'markers',
      x: [d.summary.center_x_zero_mm], y: [d.summary.center_angle_deg],
      marker: {{size: 12, color: '#111', symbol: 'x'}},
      name: 'best center'
    }}], heatLayout(`Circular template score  best=${{Number(d.summary.template_score).toFixed(4)}}`), {{responsive:true}});

    Plotly.newPlot('circleMatch', [{{
      type: 'heatmap', x: d.edgeGrid.axes, y: d.edgeGrid.angles, z: d.edgeGrid.edge,
      colorscale: 'Greys', colorbar: {{title: 'edge'}}
    }}, {{
      type: 'scatter', mode: 'lines',
      x: d.boundary.x, y: d.boundary.angle,
      line: {{color: '#ff2d2d', width: 3}},
      name: 'matched circular edge'
    }}, {{
      type: 'scatter', mode: 'markers',
      x: [d.summary.center_x_zero_mm], y: [d.summary.center_angle_deg],
      marker: {{size: 11, color: '#008cff'}},
      name: 'detected center'
    }}], heatLayout(`Detected circle  center x=${{Number(d.summary.center_x_zero_mm).toFixed(1)}} mm, angle=${{Number(d.summary.center_angle_deg).toFixed(1)}} deg`), {{responsive:true}});

    Plotly.newPlot('dent3d', [{{
      type: 'scatter3d', mode: 'markers',
      x: d.dent.outside.x, y: d.dent.outside.y, z: d.dent.outside.z,
      marker: {{size: 1.8, color: d.dent.outside.c, colorscale: colorScale, reversescale: true, opacity: 0.38, colorbar: {{title: 'out mm'}}}},
      name: 'outside'
    }}, {{
      type: 'scatter3d', mode: 'markers',
      x: d.dent.inside.x, y: d.dent.inside.y, z: d.dent.inside.z,
      marker: {{size: 3.2, color: '#ff9d00', opacity: 0.86}},
      name: 'dent area'
    }}, {{
      type: 'scatter3d', mode: 'markers',
      x: d.dent.edge.x, y: d.dent.edge.y, z: d.dent.edge.z,
      marker: {{size: 4.0, color: '#ff2020', opacity: 0.95}},
      name: 'edge band'
    }}, {{
      type: 'scatter3d', mode: 'lines',
      x: d.boundary.x, y: d.boundary.y, z: d.boundary.z,
      line: {{color: '#111', width: 7}},
      name: 'known-diameter boundary'
    }}], {{
      title: 'Detected dent region',
      scene: {{...sceneCommon, xaxis: {{title: 'x mm'}}}},
      margin: {{l:0,r:0,t:42,b:0}},
      legend: {{x:0.02, y:0.98}}
    }}, {{responsive:true}});
  </script>
</body>
</html>
"""
    output.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create combined pipe-analysis HTML for one experiment.")
    parser.add_argument("--raw-point-cloud", required=True)
    parser.add_argument("--axis-point-cloud", required=True)
    parser.add_argument("--edge-grid", required=True)
    parser.add_argument("--boundary", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--dent-region", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", default="Pipe Measurement Combined Analysis")
    parser.add_argument("--result-image")
    parser.add_argument("--result-offset")
    parser.add_argument("--result-correction")
    parser.add_argument("--result-note")
    args = parser.parse_args()

    raw_path = Path(args.raw_point_cloud)
    axis_path = Path(args.axis_point_cloud)
    edge_path = Path(args.edge_grid)
    boundary_path = Path(args.boundary)
    summary_path = Path(args.summary)
    dent_path = Path(args.dent_region)

    edge_grid = read_edge_grid(edge_path)
    boundary = read_boundary(boundary_path, float(edge_grid["axisOffset"]))
    summary = read_summary(summary_path)
    if "center_axis_s_mm" in summary:
        summary["center_x_zero_mm"] = float(summary["center_axis_s_mm"]) - float(edge_grid["axisOffset"])

    result: dict[str, object] = {}
    if args.result_image:
        result = {
            "image": Path(args.result_image).name,
            "description": "推定された窪み中心へレーザーを照射した確認写真です。",
            "center_x": f"{float(summary.get('center_x_zero_mm', 0.0)):.1f}",
            "center_angle": f"{float(summary.get('center_angle_deg', 0.0)):.1f}",
            "laser_definition": "白い中心コアをレーザー位置として使用",
            "offset": args.result_offset or "",
            "correction": args.result_correction or "",
            "note": args.result_note or "写真からの概算のため、カメラ角度やシートのしわで数mm程度の誤差があります。",
        }

    payload = {
        "title": args.title,
        "raw": read_points(raw_path, "x_mm", "y_mm", "z_mm", "outward_mm"),
        "axis": read_points(axis_path, "x_zero_mm", "axis_y_mm", "axis_z_mm", "fitted_outward_mm"),
        "edgeGrid": edge_grid,
        "templateScore": make_template_score(edge_grid, summary),
        "boundary": boundary,
        "summary": summary,
        "dent": read_dent_region(dent_path),
        "result": result,
    }
    output = Path(args.output)
    write_html(payload, output)
    print(f"Wrote combined HTML: {output}")
    print(f"Detected center x_zero: {summary.get('center_x_zero_mm', '')} mm")
    print(f"Detected center angle: {summary.get('center_angle_deg', '')} deg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
