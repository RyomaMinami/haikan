"""
Create an RViz-like realtime browser viewer for pipe scan CSV data.

This viewer is intended for live use during auto_angle_wheel_scan_logger.py:
    - reads the growing CSV periodically
    - updates a 3D point cloud
    - updates an unwrapped angle-x map
    - shows current angle, row count, point count, x range, and latest DL50/LK
    - preserves the user's 3D camera while refreshing

Serve sensor_logger with:
    python -m http.server 8765 --bind 127.0.0.1
Then open the generated HTML through http://127.0.0.1:8765/...
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def write_html(csv_name: str, output_path: Path, base_radius_mm: float, invert_lk: bool, refresh_ms: int) -> None:
    config = {
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
  <title>RViz-like Live Pipe Viewer</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    :root {{
      --bg: #15171a;
      --panel: #20242a;
      --panel2: #111316;
      --text: #e9edf1;
      --muted: #9aa4af;
      --line: #363d46;
      --accent: #4fb3ff;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, sans-serif; background: var(--bg); color: var(--text); overflow: hidden; }}
    .app {{ display: grid; grid-template-columns: 300px 1fr; height: 100vh; }}
    aside {{ background: var(--panel); border-right: 1px solid var(--line); padding: 14px; overflow: auto; }}
    main {{ display: grid; grid-template-rows: 1fr 36%; min-width: 0; }}
    #cloud {{ background: var(--panel2); border-bottom: 1px solid var(--line); }}
    #map {{ background: #fff; }}
    h1 {{ font-size: 18px; margin: 0 0 12px; }}
    h2 {{ font-size: 13px; margin: 18px 0 8px; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; }}
    .stat {{ display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,.06); font-size: 13px; }}
    .stat span:first-child {{ color: var(--muted); }}
    .stat span:last-child {{ font-variant-numeric: tabular-nums; }}
    .pill {{ display: inline-block; padding: 3px 8px; border-radius: 999px; background: #14324a; color: #bfe4ff; }}
    label {{ display: flex; justify-content: space-between; gap: 10px; align-items: center; margin: 10px 0; font-size: 13px; color: var(--muted); }}
    input[type="range"] {{ width: 130px; }}
    button {{ width: 100%; padding: 8px 10px; margin: 6px 0; border: 1px solid var(--line); background: #2a3038; color: var(--text); border-radius: 4px; cursor: pointer; }}
    button:hover {{ border-color: var(--accent); }}
    .small {{ color: var(--muted); font-size: 12px; line-height: 1.45; }}
    @media (max-width: 900px) {{
      .app {{ grid-template-columns: 1fr; grid-template-rows: auto 1fr; }}
      aside {{ max-height: 280px; }}
    }}
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h1>Live Pipe Viewer</h1>
      <div class="small">CSVを監視して点群を更新します。3Dカメラは更新後も保持されます。</div>

      <h2>Status</h2>
      <div class="stat"><span>state</span><span id="state" class="pill">waiting</span></div>
      <div class="stat"><span>rows</span><span id="rows">0</span></div>
      <div class="stat"><span>points</span><span id="points">0</span></div>
      <div class="stat"><span>angles</span><span id="angles">0</span></div>
      <div class="stat"><span>latest angle</span><span id="latestAngle">-</span></div>
      <div class="stat"><span>latest DL50</span><span id="latestDl50">-</span></div>
      <div class="stat"><span>latest LK1</span><span id="latestLk">-</span></div>
      <div class="stat"><span>x range</span><span id="xRange">-</span></div>
      <div class="stat"><span>updated</span><span id="updated">-</span></div>

      <h2>View</h2>
      <label>point size <input id="pointSize" type="range" min="1" max="7" step="0.5" value="3"></label>
      <label>opacity <input id="opacity" type="range" min="0.2" max="1" step="0.05" value="0.9"></label>
      <label><span>show heatmap</span><input id="showMap" type="checkbox" checked></label>
      <button id="resetCamera">Reset camera</button>
      <button id="pause">Pause</button>

      <h2>Input</h2>
      <div class="small" id="csvName"></div>
    </aside>
    <main>
      <div id="cloud"></div>
      <div id="map"></div>
    </main>
  </div>

  <script>
    const config = {json.dumps(config)};
    let paused = false;
    let lastCamera = null;
    let initialized = false;

    csvName.textContent = config.csvName;

    function parseCsv(text) {{
      const lines = text.trim().split(/\\r?\\n/).filter(Boolean);
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
      const i = Math.floor(v.length / 2);
      return v.length % 2 ? v[i] : (v[i - 1] + v[i]) / 2;
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
        raw.push({{angle, x, lk, dl50: num(row.dl50_hi_mm)}});
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
          outward,
          lk: p.lk,
          dl50: p.dl50
        }};
      }});
    }}

    function buildHeat(points) {{
      const angles = [...new Set(points.map(p => p.angle))].sort((a,b) => a-b);
      const xs = [...new Set(points.map(p => Math.round(p.x * 10) / 10))].sort((a,b) => a-b);
      const m = new Map();
      for (const p of points) m.set(`${{p.angle}}:${{Math.round(p.x * 10) / 10}}`, p.outward);
      const z = angles.map(a => xs.map(x => m.has(`${{a}}:${{x}}`) ? m.get(`${{a}}:${{x}}`) : null));
      return {{angles, xs, z}};
    }}

    function setStatus(id, value) {{ document.getElementById(id).textContent = value; }}

    async function update() {{
      if (paused) return;
      try {{
        const res = await fetch(config.csvName + '?t=' + Date.now());
        if (!res.ok) throw new Error('CSV not found yet');
        const text = await res.text();
        const rows = parseCsv(text);
        const points = buildPoints(rows);
        const heat = buildHeat(points);
        const latest = points.length ? points[points.length - 1] : null;
        const xs = points.map(p => p.x).filter(Number.isFinite);

        setStatus('state', 'live');
        setStatus('rows', rows.length);
        setStatus('points', points.length);
        setStatus('angles', heat.angles.length);
        setStatus('latestAngle', latest ? latest.angle.toFixed(1) + ' deg' : '-');
        setStatus('latestDl50', latest?.dl50 != null ? latest.dl50.toFixed(3) + ' mm' : '-');
        setStatus('latestLk', latest ? latest.lk.toFixed(3) + ' mm' : '-');
        setStatus('xRange', xs.length ? `${{Math.min(...xs).toFixed(1)}} - ${{Math.max(...xs).toFixed(1)}} mm` : '-');
        setStatus('updated', new Date().toLocaleTimeString());

        const pointSize = Number(document.getElementById('pointSize').value);
        const opacity = Number(document.getElementById('opacity').value);
        const cloud = document.getElementById('cloud');
        if (cloud._fullLayout?.scene?.camera) lastCamera = cloud._fullLayout.scene.camera;

        const layout3d = {{
          title: 'Realtime point cloud',
          paper_bgcolor: '#111316',
          plot_bgcolor: '#111316',
          font: {{color: '#e9edf1'}},
          uirevision: 'keep-camera',
          scene: {{
            xaxis: {{title: 'x mm', gridcolor: '#3a414a', zerolinecolor: '#666'}},
            yaxis: {{title: 'y mm', gridcolor: '#3a414a', zerolinecolor: '#666'}},
            zaxis: {{title: 'z mm', gridcolor: '#3a414a', zerolinecolor: '#666'}},
            aspectmode: 'data',
            camera: lastCamera ?? {{eye: {{x: 1.7, y: 1.4, z: 0.9}}, up: {{x: 0, y: 0, z: 1}}}}
          }},
          margin: {{l: 0, r: 0, t: 42, b: 0}}
        }};

        Plotly.react('cloud', [{{
          type: 'scatter3d',
          mode: 'markers',
          x: points.map(p => p.x),
          y: points.map(p => p.y),
          z: points.map(p => p.z),
          text: points.map(p => `x=${{p.x.toFixed(2)}} mm<br>angle=${{p.angle.toFixed(1)}} deg<br>out=${{p.outward.toFixed(3)}} mm`),
          hoverinfo: 'text',
          marker: {{
            size: pointSize,
            opacity,
            color: points.map(p => p.outward),
            colorscale: 'Turbo',
            colorbar: {{title: 'out mm'}}
          }}
        }}], layout3d, {{responsive: true}});

        const showMap = document.getElementById('showMap').checked;
        document.getElementById('map').style.display = showMap ? 'block' : 'none';
        if (showMap) {{
          Plotly.react('map', [{{
            type: 'heatmap',
            x: heat.xs,
            y: heat.angles,
            z: heat.z,
            colorscale: 'Turbo',
            colorbar: {{title: 'out mm'}}
          }}], {{
            title: 'Realtime unwrapped map',
            xaxis: {{title: 'x mm'}},
            yaxis: {{title: 'angle deg'}},
            margin: {{l: 70, r: 20, t: 42, b: 55}},
            uirevision: 'keep-map'
          }}, {{responsive: true}});
        }}
        initialized = true;
      }} catch (err) {{
        setStatus('state', 'waiting');
        setStatus('updated', String(err.message || err));
      }}
    }}

    document.getElementById('resetCamera').addEventListener('click', () => {{
      lastCamera = {{eye: {{x: 1.7, y: 1.4, z: 0.9}}, up: {{x: 0, y: 0, z: 1}}}};
      initialized = false;
      update();
    }});
    document.getElementById('pause').addEventListener('click', (e) => {{
      paused = !paused;
      e.target.textContent = paused ? 'Resume' : 'Pause';
      setStatus('state', paused ? 'paused' : 'live');
      if (!paused) update();
    }});
    document.getElementById('pointSize').addEventListener('input', update);
    document.getElementById('opacity').addEventListener('input', update);
    document.getElementById('showMap').addEventListener('change', update);

    update();
    setInterval(update, config.refreshMs);
  </script>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create RViz-like realtime pipe scan viewer.")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output-html")
    parser.add_argument("--base-radius-mm", type=float, default=120.0)
    parser.add_argument("--invert-lk", action="store_true")
    parser.add_argument("--refresh-ms", type=int, default=700)
    args = parser.parse_args()

    csv_path = Path(args.csv)
    output = Path(args.output_html) if args.output_html else csv_path.with_name(f"{csv_path.stem}_rviz_live.html")
    write_html(csv_path.name, output, args.base_radius_mm, args.invert_lk, args.refresh_ms)
    print(f"Wrote RViz-like realtime HTML: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
