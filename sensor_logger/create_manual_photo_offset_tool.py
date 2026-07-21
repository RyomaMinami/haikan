#!/usr/bin/env python3
"""
Create a browser-based manual correction tool for photo offset analysis.

The generated HTML shows each photo on a canvas. Drag:
  - blue point: dent/hole center
  - green point: laser center

The scale is initialized from the automatically fitted yellow ellipse. Results
are stored in browser localStorage and can be exported as CSV from the page.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
from pathlib import Path


DEFAULT_AUTO_CSV = Path("pipe154_resolution_photo_analysis_v2") / "resolution_photo_offsets.csv"
DEFAULT_OUTPUT = Path("pipe154_resolution_photo_analysis_v2") / "manual_photo_offset_tool.html"


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def image_data_url(path: Path) -> str:
    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def parse_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def build_items(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    items = []
    for index, row in enumerate(rows, start=1):
        photo = Path(row["photo"])
        if not photo.exists():
            raise SystemExit(f"Photo not found: {photo}")
        major = parse_float(row.get("ellipse_major_px"))
        minor = parse_float(row.get("ellipse_minor_px"))
        # Estimate a conservative visible radius in pixels. Manual offsets are
        # computed with this mean-radius scale by default.
        mean_diameter = 0.5 * (major + minor) if major and minor else major or minor
        items.append(
            {
                "index": index,
                "condition": row.get("condition", f"photo{index:02d}"),
                "angleStepDeg": parse_float(row.get("angle_step_deg")),
                "xStepMm": parse_float(row.get("x_step_mm")),
                "pointCloudErrorMm": parse_float(row.get("point_cloud_error_mm")),
                "photoName": photo.name,
                "image": image_data_url(photo),
                "dentX": parse_float(row.get("dent_center_x_px")),
                "dentY": parse_float(row.get("dent_center_y_px")),
                "laserX": parse_float(row.get("laser_x_px")),
                "laserY": parse_float(row.get("laser_y_px")),
                "scalePxPerMm": mean_diameter / 154.0 if mean_diameter else 1.0,
            }
        )
    return items


def write_html(path: Path, items: list[dict[str, object]]) -> None:
    items_json = json.dumps(items, ensure_ascii=False)
    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Manual Photo Offset Tool</title>
  <style>
    body {{ margin: 0; font-family: Arial, "Meiryo", sans-serif; background: #f6f8fa; color: #202124; }}
    header {{ padding: 12px 16px; background: #111827; color: white; position: sticky; top: 0; z-index: 2; }}
    main {{ display: grid; grid-template-columns: 1fr 360px; gap: 14px; padding: 14px; }}
    canvas {{ width: 100%; max-height: calc(100vh - 120px); object-fit: contain; background: #111; border: 1px solid #d0d7de; }}
    aside {{ background: white; border: 1px solid #d0d7de; border-radius: 6px; padding: 12px; }}
    button {{ padding: 8px 10px; margin: 3px; border: 1px solid #9ca3af; background: #fff; border-radius: 4px; cursor: pointer; }}
    button.primary {{ background: #2563eb; border-color: #2563eb; color: white; }}
    button.warn {{ background: #f59e0b; border-color: #f59e0b; color: #111; }}
    input {{ width: 90px; padding: 5px; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 10px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 4px 5px; text-align: right; }}
    th:first-child, td:first-child {{ text-align: left; }}
    .row {{ margin: 8px 0; }}
    .note {{ font-size: 12px; line-height: 1.5; color: #374151; background: #fff8c5; border: 1px solid #d4a72c; padding: 8px; border-radius: 4px; }}
    .value {{ font-size: 22px; font-weight: 700; }}
  </style>
</head>
<body>
<header>
  <strong>Manual Photo Offset Tool</strong>
  <span id="title"></span>
</header>
<main>
  <section>
    <canvas id="canvas"></canvas>
  </section>
  <aside>
    <div class="note">
      Drag blue point to the hole center. Drag green point to the laser center.
      Use the white core as the laser center if it is visible. Results are kept in this browser.
    </div>
    <div class="row">
      <button id="prev">Prev</button>
      <button id="next" class="primary">Next</button>
      <button id="reset" class="warn">Reset Current</button>
    </div>
    <div class="row">
      Dent diameter:
      <input id="diameter" type="number" value="154" step="0.1"> mm
    </div>
    <div class="row">
      Scale:
      <input id="scale" type="number" step="0.001"> px/mm
    </div>
    <div class="row">
      <div>Manual offset</div>
      <div class="value"><span id="offset">0.00</span> mm</div>
      <div>dx=<span id="dx">0.00</span> mm, dy=<span id="dy">0.00</span> mm</div>
    </div>
    <div class="row">
      <button id="exportCsv" class="primary">Export CSV</button>
      <button id="copyCsv">Copy CSV</button>
    </div>
    <table id="table"></table>
  </aside>
</main>

<script>
const rawItems = {items_json};
const storageKey = "pipe154_manual_photo_offsets_v1";
let items = loadState();
let idx = 0;
let dragging = null;
const canvas = document.getElementById("canvas");
const ctx = canvas.getContext("2d");
const img = new Image();

function loadState() {{
  const saved = localStorage.getItem(storageKey);
  if (saved) {{
    try {{
      const values = JSON.parse(saved);
      return rawItems.map((item, i) => Object.assign({{}}, item, values[i] || {{}}));
    }} catch (e) {{}}
  }}
  return rawItems.map(item => Object.assign({{}}, item));
}}

function saveState() {{
  const values = items.map(item => ({{
    dentX: item.dentX, dentY: item.dentY,
    laserX: item.laserX, laserY: item.laserY,
    scalePxPerMm: item.scalePxPerMm
  }}));
  localStorage.setItem(storageKey, JSON.stringify(values));
}}

function current() {{ return items[idx]; }}

function loadImage() {{
  const item = current();
  img.onload = () => {{
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    draw();
  }};
  img.src = item.image;
  document.getElementById("scale").value = item.scalePxPerMm.toFixed(4);
  document.getElementById("title").textContent = ` - ${{idx + 1}}/${{items.length}} ${{item.condition}}`;
}}

function drawPoint(x, y, color, label) {{
  ctx.beginPath();
  ctx.arc(x, y, 18, 0, Math.PI * 2);
  ctx.lineWidth = 6;
  ctx.strokeStyle = "white";
  ctx.stroke();
  ctx.lineWidth = 4;
  ctx.strokeStyle = color;
  ctx.stroke();
  ctx.font = "42px Arial";
  ctx.fillStyle = color;
  ctx.fillText(label, x + 22, y - 22);
}}

function draw() {{
  const item = current();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0);
  ctx.beginPath();
  ctx.moveTo(item.dentX, item.dentY);
  ctx.lineTo(item.laserX, item.laserY);
  ctx.lineWidth = 5;
  ctx.strokeStyle = "white";
  ctx.stroke();
  drawPoint(item.dentX, item.dentY, "#2563eb", "hole");
  drawPoint(item.laserX, item.laserY, "#16a34a", "laser");
  updateReadout();
  renderTable();
}}

function offsetFor(item) {{
  const scale = Number(item.scalePxPerMm || 1);
  const dx = (item.laserX - item.dentX) / scale;
  const dy = (item.laserY - item.dentY) / scale;
  return {{ dx, dy, offset: Math.hypot(dx, dy) }};
}}

function updateReadout() {{
  const item = current();
  const v = offsetFor(item);
  document.getElementById("offset").textContent = v.offset.toFixed(2);
  document.getElementById("dx").textContent = v.dx.toFixed(2);
  document.getElementById("dy").textContent = v.dy.toFixed(2);
}}

function renderTable() {{
  const ref = offsetFor(items[0]).offset;
  let html = "<tr><th>condition</th><th>offset</th><th>change</th><th>pc</th></tr>";
  for (const item of items) {{
    const v = offsetFor(item).offset;
    html += `<tr><td>${{item.condition}}</td><td>${{v.toFixed(2)}}</td><td>${{Math.abs(v-ref).toFixed(2)}}</td><td>${{Number(item.pointCloudErrorMm).toFixed(2)}}</td></tr>`;
  }}
  document.getElementById("table").innerHTML = html;
}}

function canvasPoint(evt) {{
  const rect = canvas.getBoundingClientRect();
  return {{
    x: (evt.clientX - rect.left) * canvas.width / rect.width,
    y: (evt.clientY - rect.top) * canvas.height / rect.height
  }};
}}

canvas.addEventListener("pointerdown", evt => {{
  const p = canvasPoint(evt);
  const item = current();
  const dDent = Math.hypot(p.x - item.dentX, p.y - item.dentY);
  const dLaser = Math.hypot(p.x - item.laserX, p.y - item.laserY);
  dragging = dDent < dLaser ? "dent" : "laser";
  canvas.setPointerCapture(evt.pointerId);
}});

canvas.addEventListener("pointermove", evt => {{
  if (!dragging) return;
  const p = canvasPoint(evt);
  const item = current();
  if (dragging === "dent") {{
    item.dentX = p.x; item.dentY = p.y;
  }} else {{
    item.laserX = p.x; item.laserY = p.y;
  }}
  saveState();
  draw();
}});

canvas.addEventListener("pointerup", evt => {{
  dragging = null;
}});

document.getElementById("prev").onclick = () => {{
  idx = Math.max(0, idx - 1);
  loadImage();
}};

document.getElementById("next").onclick = () => {{
  idx = Math.min(items.length - 1, idx + 1);
  loadImage();
}};

document.getElementById("reset").onclick = () => {{
  items[idx] = Object.assign({{}}, rawItems[idx]);
  saveState();
  loadImage();
}};

document.getElementById("scale").onchange = evt => {{
  current().scalePxPerMm = Number(evt.target.value);
  saveState();
  draw();
}};

document.getElementById("diameter").onchange = evt => {{
  const d = Number(evt.target.value || 154);
  for (let i = 0; i < items.length; i++) {{
    const raw = rawItems[i];
    const diameterPx = raw.scalePxPerMm * 154;
    items[i].scalePxPerMm = diameterPx / d;
  }}
  saveState();
  loadImage();
}};

function csvText() {{
  const ref = offsetFor(items[0]).offset;
  const lines = ["condition,angle_step_deg,x_step_mm,manual_offset_mm,manual_change_vs_a5_x1_mm,manual_dx_mm,manual_dy_mm,point_cloud_error_mm,dent_x_px,dent_y_px,laser_x_px,laser_y_px"];
  for (const item of items) {{
    const v = offsetFor(item);
    lines.push([item.condition, item.angleStepDeg, item.xStepMm, v.offset.toFixed(4), Math.abs(v.offset-ref).toFixed(4), v.dx.toFixed(4), v.dy.toFixed(4), Number(item.pointCloudErrorMm).toFixed(4), item.dentX.toFixed(2), item.dentY.toFixed(2), item.laserX.toFixed(2), item.laserY.toFixed(2)].join(","));
  }}
  return lines.join("\\n");
}}

document.getElementById("copyCsv").onclick = async () => {{
  await navigator.clipboard.writeText(csvText());
  alert("CSV copied");
}};

document.getElementById("exportCsv").onclick = () => {{
  const blob = new Blob([csvText()], {{type: "text/csv"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "manual_photo_offsets.csv";
  a.click();
}};

loadImage();
</script>
</body>
</html>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create manual photo center correction HTML.")
    parser.add_argument("--auto-csv", type=Path, default=DEFAULT_AUTO_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    if not args.auto_csv.is_absolute():
        args.auto_csv = script_dir / args.auto_csv
    if not args.output.is_absolute():
        args.output = script_dir / args.output

    rows = read_rows(args.auto_csv)
    items = build_items(rows)
    write_html(args.output, items)
    print(f"Wrote manual tool: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
