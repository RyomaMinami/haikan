"""
Create a browser-based realtime view for an auto scan CSV.

The generated HTML periodically fetches the CSV and redraws:
    - a 3D point cloud
    - an unwrapped angle-x heatmap

It is intended to be served from the sensor_logger directory:
    python -m http.server 8765 --bind 127.0.0.1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def write_html(csv_name: str, output_path: Path, base_radius_mm: float, invert_lk: bool, refresh_ms: int) -> None:
    payload = {
        "csvName": csv_name,
        "baseRadiusMm": base_radius_mm,
        "invertLk": invert_lk,
        "refreshMs": refresh_ms,
    }
    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Realtime Pipe Scan</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f5f5f2; color: #222; }}
    header {{ padding: 12px 16px; background: #fff; border-bottom: 1px solid #d8d8d2; position: sticky; top: 0; z-index: 5; }}
    h1 {{ margin: 0 0 4px; font-size: 18px; }}
    #meta {{ color: #555; font-size: 13px; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; padding: 10px; }}
    .plot {{ min-height: 620px; background: #fff; border: 1px solid #ddd; }}
    @media (max-width: 1000px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Realtime Pipe Scan</h1>
    <div id="meta">waiting for CSV...</div>
  </header>
  <div class="grid">
    <div id="cloud" class="plot"></div>
    <div id="heatmap" class="plot"></div>
  </div>
  <script>
    const config = {json.dumps(payload)};

    function parseCsv(text) {{
      const lines = text.trim().split(/\\r?\\n/);
      if (lines.length < 2) return [];
      const headers = lines[0].split(',').map(x => x.trim().replace(/^\\uFEFF/, ''));
      return lines.slice(1).map(line => {{
        const cols = line.split(',');
        const row = {{}};
        headers.forEach((h, i) => row[h] = cols[i] ?? '');
        return row;
      }});
    }}

    function num(v) {{
      const n = Number(v);
      return Number.isFinite(n) ? n : null;
    }}

    function median(values) {{
      const v = values.filter(Number.isFinite).sort((a, b) => a - b);
      if (!v.length) return 0;
      const mid = Math.floor(v.length / 2);
      return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
    }}

    function buildPoints(rows) {{
      const raw = [];
      const lkValues = [];
      const seen = new Set();
      for (const row of rows) {{
        const angle = num(row.angle_deg);
        const x = num(row.dl50_delta_mm) ?? num(row.target_delta_mm) ?? num(row.dl50_progress_mm);
        const lk = num(row.lk_out1_mm);
        if (angle === null || x === null || lk === null) continue;
        const key = `${{Math.round(angle * 1000)}}:${{Math.round(x * 1000)}}`;
        if (seen.has(key)) continue;
        seen.add(key);
        raw.push({{angle, x, lk}});
        lkValues.push(lk);
      }}
      const ref = median(lkValues);
      const sign = config.invertLk ? -1 : 1;
      return raw.map(p => {{
        const outward = sign * (p.lk - ref);
        const r = config.baseRadiusMm + outward;
        const theta = p.angle * Math.PI / 180;
        return {{
          x: p.x,
          y: r * Math.cos(theta),
          z: r * Math.sin(theta),
          angle: p.angle,
          outward
        }};
      }});
    }}

    function buildHeat(points) {{
      const angles = [...new Set(points.map(p => p.angle))].sort((a, b) => a - b);
      const xs = [...new Set(points.map(p => Math.round(p.x * 10) / 10))].sort((a, b) => a - b);
      const map = new Map();
      for (const p of points) map.set(`${{p.angle}}:${{Math.round(p.x * 10) / 10}}`, p.outward);
      const z = angles.map(a => xs.map(x => map.has(`${{a}}:${{x}}`) ? map.get(`${{a}}:${{x}}`) : null));
      return {{angles, xs, z}};
    }}

    async function update() {{
      try {{
        const res = await fetch(config.csvName + '?t=' + Date.now());
        if (!res.ok) throw new Error('CSV not available yet');
        const text = await res.text();
        const rows = parseCsv(text);
        const points = buildPoints(rows);
        const heat = buildHeat(points);
        document.getElementById('meta').textContent =
          `${{config.csvName}} | rows=${{rows.length}} | points=${{points.length}} | angles=${{heat.angles.length}} | updated=${{new Date().toLocaleTimeString()}}`;

        Plotly.react('cloud', [{{
          type: 'scatter3d', mode: 'markers',
          x: points.map(p => p.x), y: points.map(p => p.y), z: points.map(p => p.z),
          marker: {{size: 3, color: points.map(p => p.outward), colorscale: 'Turbo', colorbar: {{title: 'out mm'}}}},
          name: 'realtime points'
        }}], {{
          title: 'Realtime 3D point cloud',
          scene: {{
            xaxis: {{title: 'x mm'}},
            yaxis: {{title: 'y mm'}},
            zaxis: {{title: 'z mm'}},
            aspectmode: 'data'
          }},
          margin: {{l:0,r:0,t:42,b:0}}
        }}, {{responsive:true}});

        Plotly.react('heatmap', [{{
          type: 'heatmap',
          x: heat.xs, y: heat.angles, z: heat.z,
          colorscale: 'Turbo',
          colorbar: {{title: 'out mm'}}
        }}], {{
          title: 'Realtime unwrapped map',
          xaxis: {{title: 'x mm'}},
          yaxis: {{title: 'angle deg'}},
          margin: {{l:68,r:20,t:42,b:55}}
        }}, {{responsive:true}});
      }} catch (err) {{
        document.getElementById('meta').textContent = String(err.message || err);
      }}
    }}

    update();
    setInterval(update, config.refreshMs);
  </script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create realtime scan HTML.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output-html")
    parser.add_argument("--base-radius-mm", type=float, default=120.0)
    parser.add_argument("--invert-lk", action="store_true")
    parser.add_argument("--refresh-ms", type=int, default=2000)
    args = parser.parse_args()

    csv_path = Path(args.csv)
    output = Path(args.output_html) if args.output_html else csv_path.with_name(f"{csv_path.stem}_realtime.html")
    write_html(csv_path.name, output, args.base_radius_mm, args.invert_lk, args.refresh_ms)
    print(f"Wrote realtime HTML: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
