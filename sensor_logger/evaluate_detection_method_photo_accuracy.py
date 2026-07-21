#!/usr/bin/env python3
"""Evaluate detection-method target accuracy from mapped outside photos."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np

from analyze_resolution_photo_sequence import (
    cylindrical_offset_mm,
    detect_laser_center,
    fit_yellow_ellipse,
)


DEFAULT_MAPPING = (
    Path("pipe154_detection_method_photo_mapping_20260609_pictures")
    / "photo_target_mapping_gap_inferred.csv"
)
DEFAULT_OUTPUT_DIR = Path("pipe154_detection_method_photo_accuracy_20260609")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def draw_cross(img: np.ndarray, point: tuple[int, int], color: tuple[int, int, int], size: int = 14) -> None:
    x, y = point
    cv2.line(img, (x - size, y), (x + size, y), color, 2, cv2.LINE_AA)
    cv2.line(img, (x, y - size), (x, y + size), color, 2, cv2.LINE_AA)
    cv2.circle(img, point, 3, color, -1, cv2.LINE_AA)


def annotate(
    image: np.ndarray,
    out_path: Path,
    label: str,
    ellipse_info: dict[str, object] | None,
    laser_center: np.ndarray | None,
    offset_mm: float | None,
    within_limit: bool,
) -> None:
    annotated = image.copy()
    if ellipse_info is not None:
        cv2.ellipse(annotated, ellipse_info["ellipse"], (0, 255, 255), 4)
        center = tuple(np.round(ellipse_info["center"]).astype(int))
        draw_cross(annotated, center, (255, 0, 0))
    else:
        center = None

    if laser_center is not None:
        laser = tuple(np.round(laser_center).astype(int))
        draw_cross(annotated, laser, (0, 255, 0))
        if center is not None:
            cv2.line(annotated, center, laser, (255, 255, 255), 2, cv2.LINE_AA)

    status = "OK <=20mm" if within_limit else "NG"
    if offset_mm is None:
        offset_text = "laser not visible / outside"
    else:
        offset_text = f"offset={offset_mm:.1f}mm"
    cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 96), (0, 0, 0), -1)
    cv2.putText(annotated, label[:90], (24, 36), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(annotated, f"{offset_text}  {status}", (24, 76), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(out_path), annotated)


def analyze_row(
    row: dict[str, str],
    out_dir: Path,
    dent_diameter_mm: float,
    pipe_diameter_mm: float,
    pass_limit_mm: float,
) -> dict[str, object]:
    photo = Path(row["photo_path"])
    result: dict[str, object] = dict(row)
    result["photo"] = str(photo)
    result["laser_visible"] = False
    result["photo_offset_mm"] = ""
    result["photo_axial_mm"] = ""
    result["photo_circumferential_mm"] = ""
    result["within_20mm"] = False
    result["analysis_error"] = ""

    image = cv2.imread(str(photo))
    if image is None:
        result["analysis_error"] = "could not read image"
        return result

    ellipse_info = None
    laser_center = None
    offset_mm = None
    try:
        ellipse_info = fit_yellow_ellipse(image)
        opening_mask = np.zeros(image.shape[:2], dtype=np.uint8)
        cv2.drawContours(opening_mask, [ellipse_info["contour"]], -1, 255, thickness=-1)
        laser_center, _, _ = detect_laser_center(image, opening_mask)
        axial_mm, circumferential_mm, offset_mm = cylindrical_offset_mm(
            ellipse_info["center"],
            laser_center,
            ellipse_info["major_px"],
            ellipse_info["minor_px"],
            ellipse_info["major_angle_deg"],
            dent_diameter_mm,
            pipe_diameter_mm,
        )
        result["laser_visible"] = True
        result["photo_offset_mm"] = offset_mm
        result["photo_axial_mm"] = axial_mm
        result["photo_circumferential_mm"] = circumferential_mm
        result["dent_center_x_px"] = float(ellipse_info["center"][0])
        result["dent_center_y_px"] = float(ellipse_info["center"][1])
        result["laser_x_px"] = float(laser_center[0])
        result["laser_y_px"] = float(laser_center[1])
        result["ellipse_major_px"] = ellipse_info["major_px"]
        result["ellipse_minor_px"] = ellipse_info["minor_px"]
    except Exception as exc:
        result["analysis_error"] = str(exc)

    within = offset_mm is not None and offset_mm <= pass_limit_mm
    result["within_20mm"] = within
    annotation = out_dir / f"{int(row['target_index']):03d}_{row['condition']}_{photo.stem}_annotated.jpg"
    annotate(
        image,
        annotation,
        f"{row['target_index']} {row['condition']}",
        ellipse_info,
        laser_center,
        offset_mm,
        within,
    )
    result["annotated_image"] = str(annotation)
    return result


def summarize(rows: list[dict[str, object]], pass_limit_mm: float) -> list[dict[str, object]]:
    methods = []
    for row in rows:
        method = str(row.get("method", ""))
        if method not in methods:
            methods.append(method)

    summary = []
    for method in methods:
        subset = [row for row in rows if row.get("method") == method]
        offsets = [float(row["photo_offset_mm"]) for row in subset if str(row["photo_offset_mm"]) not in ("", "nan")]
        pass_count = sum(1 for row in subset if str(row.get("within_20mm", "")).lower() == "true")
        visible_count = sum(1 for row in subset if str(row.get("laser_visible", "")).lower() == "true")
        total = len(subset)
        summary.append(
            {
                "method": method,
                "total": total,
                "laser_visible": visible_count,
                "laser_not_visible": total - visible_count,
                "pass_count_20mm": pass_count,
                "pass_rate_20mm_percent": 100.0 * pass_count / total if total else 0.0,
                "mean_visible_offset_mm": float(np.mean(offsets)) if offsets else "",
                "median_visible_offset_mm": float(np.median(offsets)) if offsets else "",
                "max_visible_offset_mm": float(np.max(offsets)) if offsets else "",
                "pass_limit_mm": pass_limit_mm,
            }
        )
    summary.sort(key=lambda r: (-float(r["pass_rate_20mm_percent"]), float(r["mean_visible_offset_mm"] or 9999)))
    return summary


def condition_order(rows: list[dict[str, object]]) -> list[str]:
    order = []
    for row in rows:
        cond = str(row.get("source_condition", ""))
        if cond not in order:
            order.append(cond)
    return order


def write_graph(path: Path, rows: list[dict[str, object]], pass_limit_mm: float) -> None:
    conditions = condition_order(rows)
    methods = []
    for row in rows:
        method = str(row.get("method", ""))
        if method not in methods:
            methods.append(method)

    x = np.arange(len(conditions))
    fig, ax = plt.subplots(figsize=(15, 7))
    for method in methods:
        ys = []
        for i, cond in enumerate(conditions):
            match = next((row for row in rows if row.get("method") == method and row.get("source_condition") == cond), None)
            if not match:
                ys.append(np.nan)
                continue
            value = parse_float(match.get("photo_offset_mm"))
            if value is None:
                ys.append(np.nan)
            else:
                ys.append(value)
        ax.plot(x, ys, marker="o", linewidth=2.0, label=method)

    ax.axhline(pass_limit_mm, color="#d62728", linestyle="--", linewidth=1.8, label=f"{pass_limit_mm:.0f} mm limit")
    ax.set_xticks(x)
    ax.set_xticklabels(conditions, rotation=45, ha="right")
    ax.set_ylabel("photo center error [mm]")
    ax.set_title("Photo-based accuracy by detection method and resolution")
    ax.grid(True, alpha=0.25)
    ax.legend(ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def write_summary_graph(path: Path, summary: list[dict[str, object]]) -> None:
    labels = [str(row["method"]) for row in summary]
    rates = [float(row["pass_rate_20mm_percent"]) for row in summary]
    not_visible = [int(row["laser_not_visible"]) for row in summary]
    fig, ax = plt.subplots(figsize=(9, 5))
    bars = ax.bar(labels, rates, color="#4c78a8")
    ax.set_ylim(0, 100)
    ax.set_ylabel("pass rate [%]")
    ax.set_title("Pass rate within 20 mm")
    ax.grid(True, axis="y", alpha=0.25)
    for bar, row, miss in zip(bars, summary, not_visible):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height() + 2,
            f"{int(row['pass_count_20mm'])}/{int(row['total'])}\nno laser {miss}",
            ha="center",
            va="bottom",
            fontsize=9,
        )
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def write_html(
    path: Path,
    rows: list[dict[str, object]],
    summary: list[dict[str, object]],
    line_graph: Path,
    summary_graph: Path,
) -> None:
    method_notes = {
        "max_depth": "補正後点群で最も外側に大きく出た点を中心候補にする方法。単純だが、今回の写真評価では最も20 mm以内率が高い。",
        "depth_centroid_p80": "深さが大きい上位領域を重み付き平均して中心を出す方法。面全体の偏りを拾いやすく、今回の中心移動評価では外れやすかった。",
        "edge_centroid": "くぼみ境界らしいエッジ点を平均して中心を出す方法。レーザー方向の感度差や欠けたエッジの影響を受けやすい。",
        "circular_template": "直径154 mmの円形くぼみがセンサからどう見えるかをテンプレートとして照合し、評価が高い位置を中心にする方法。以前の実験で使っていた手法。",
    }

    summary_rows = []
    for row in summary:
        mean = row["mean_visible_offset_mm"]
        mean_text = f"{float(mean):.2f}" if mean != "" else ""
        summary_rows.append(
            "<tr>"
            f"<td>{row['method']}</td>"
            f"<td>{method_notes.get(str(row['method']), '')}</td>"
            f"<td>{row['pass_count_20mm']}/{row['total']}</td>"
            f"<td>{float(row['pass_rate_20mm_percent']):.1f}</td>"
            f"<td>{row['laser_not_visible']}</td>"
            f"<td>{mean_text}</td>"
            "</tr>"
        )
    summary_rows_html = "\n".join(summary_rows)

    detail_rows = []
    for row in rows:
        offset = parse_float(row.get("photo_offset_mm"))
        offset_text = f"{offset:.2f}" if offset is not None else "レーザー非検出"
        within_text = "合格" if str(row["within_20mm"]).lower() == "true" else "不合格"
        note = str(row.get("analysis_error", ""))
        if note == "Could not find laser spot.":
            note = "レーザー光が写真内で検出できない。くぼみ外へ出た結果として不合格扱い。"
        detail_rows.append(
            "<tr>"
            f"<td>{row['target_index']}</td>"
            f"<td>{row['source_condition']}</td>"
            f"<td>{row['method']}</td>"
            f"<td>{offset_text}</td>"
            f"<td>{within_text}</td>"
            f"<td>{note}</td>"
            f"<td><a href=\"{Path(str(row['annotated_image'])).name}\">注釈画像</a></td>"
            "</tr>"
        )
    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>検出手法別 写真精度評価</title>
  <style>
    body {{ font-family: Arial, "Meiryo", sans-serif; margin: 24px; color: #202124; }}
    h1 {{ font-size: 26px; }}
    h2 {{ margin-top: 30px; }}
    img.graph {{ max-width: 100%; border: 1px solid #d0d7de; margin: 10px 0 24px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: right; }}
    th:nth-child(1), td:nth-child(1), th:nth-child(2), td:nth-child(2),
    th:nth-child(3), td:nth-child(3), th:nth-child(4), td:nth-child(4),
    th:nth-child(7), td:nth-child(7), th:nth-child(8), td:nth-child(8) {{ text-align: left; }}
    th {{ background: #f6f8fa; }}
    .note {{ background: #eef6ff; border: 1px solid #8bb8e8; padding: 10px 12px; border-radius: 6px; line-height: 1.6; }}
    .warn {{ background: #fff8c5; border-color: #d4a72c; }}
    .small {{ color: #57606a; font-size: 13px; line-height: 1.6; }}
  </style>
</head>
<body>
  <h1>検出手法別 写真精度評価</h1>
  <p class="note">
    このページは、点群データから推定した中心位置へ実際にレーザーを移動させた後、
    外側から撮影した写真を用いて精度を評価した結果です。評価値は
    <strong>写真上で推定した穴中心とレーザー白芯の距離</strong>です。
    黄色い円形部の直径を154 mm、配管外径を250 mmとして、配管表面を円筒として見た補正を入れています。
    今回は実験上の許容範囲を<strong>中心から20 mm以内</strong>とし、それ以内を合格としました。
  </p>
  <p class="note warn">
    レーザーが写真に写っていない場合は、写真処理の失敗ではなく、推定中心へ移動した結果として
    レーザーがくぼみ範囲の外へ出た可能性が高いと考えます。そのため、この集計では
    「レーザー非検出」として表に記録し、不合格扱いにしています。
    折れ線グラフでは数値化できない点は線から除外しています。
  </p>

  <h2>評価方法</h2>
  <p>
    各写真に対して、黄色い穴領域の外形を楕円フィットし、その中心を真値側の穴中心とみなしました。
    レーザー位置は赤色の広がり全体ではなく、ユーザー確認に合わせて白く明るい芯を中心として検出しています。
    その後、画像上のずれを154 mmの実寸に換算し、配管を外側から見ている影響を外径250 mmの円筒モデルで補正しています。
  </p>

  <h2>手法別の概要</h2>
  <img class="graph" src="{summary_graph.name}" alt="手法別合格率グラフ">
  <table>
    <thead><tr><th>手法</th><th>手法の考え方</th><th>20 mm以内</th><th>合格率 [%]</th><th>レーザー非検出</th><th>検出できた写真の平均誤差 [mm]</th></tr></thead>
    <tbody>{summary_rows_html}</tbody>
  </table>
  <p class="small">
    平均誤差はレーザーが検出できた写真だけで計算しています。したがって、レーザー非検出数もあわせて見る必要があります。
    例えば、平均誤差が小さくても非検出が多い手法は、実験上は安定した手法とは言えません。
  </p>

  <h2>分解能条件ごとの結果</h2>
  <p>
    横軸の <code>a5_x1</code> などは、角度間隔と移動距離間隔を表しています。
    例として <code>a5_x1</code> は角度5度間隔、軸方向1 mm間隔、<code>a20_x10</code> は角度20度間隔、軸方向10 mm間隔です。
    折れ線が20 mmの基準線より下にあるほど、写真上で穴中心に近く照射できています。
  </p>
  <img class="graph" src="{line_graph.name}" alt="分解能条件ごとの写真誤差グラフ">
  <table>
    <thead><tr><th>番号</th><th>分解能条件</th><th>手法</th><th>写真誤差 [mm]</th><th>20 mm以内</th><th>補足</th><th>注釈画像</th></tr></thead>
    <tbody>{''.join(detail_rows)}</tbody>
  </table>

  <h2>結果の読み取り</h2>
  <p>
    今回の写真評価では、<code>max_depth</code> が最も20 mm以内率が高く、
    次に <code>circular_template</code> が続きました。一方で <code>edge_centroid</code> と
    <code>depth_centroid_p80</code> は中心位置が大きくずれやすく、今回の中心移動用途には不向きな傾向があります。
    ただし、<code>max_depth</code> は最深点を使うためノイズに影響されやすい可能性があります。
    今後は <code>max_depth</code> 系と <code>circular_template</code> 系を主候補として、
    フィルタや外れ値除去を変えたときの安定性を見るのがよいと考えられます。
  </p>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate detection-method photo accuracy.")
    parser.add_argument("--mapping", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dent-diameter-mm", type=float, default=154.0)
    parser.add_argument("--pipe-diameter-mm", type=float, default=250.0)
    parser.add_argument("--pass-limit-mm", type=float, default=20.0)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    if not args.mapping.is_absolute():
        args.mapping = script_dir / args.mapping
    if not args.output_dir.is_absolute():
        args.output_dir = script_dir / args.output_dir
    args.output_dir.mkdir(parents=True, exist_ok=True)

    mapping_rows = read_csv(args.mapping)
    rows = [
        analyze_row(row, args.output_dir, args.dent_diameter_mm, args.pipe_diameter_mm, args.pass_limit_mm)
        for row in mapping_rows
        if row.get("status") == "mapped"
    ]
    summary = summarize(rows, args.pass_limit_mm)

    csv_path = args.output_dir / "detection_method_photo_accuracy.csv"
    summary_csv = args.output_dir / "detection_method_photo_accuracy_summary.csv"
    line_graph = args.output_dir / "detection_method_photo_accuracy_lines.png"
    summary_graph = args.output_dir / "detection_method_photo_accuracy_summary.png"
    html_path = args.output_dir / "detection_method_photo_accuracy.html"

    write_csv(csv_path, rows)
    write_csv(summary_csv, summary)
    write_graph(line_graph, rows, args.pass_limit_mm)
    write_summary_graph(summary_graph, summary)
    write_html(html_path, rows, summary, line_graph, summary_graph)

    print(f"Wrote: {csv_path}")
    print(f"Wrote: {summary_csv}")
    print(f"Wrote: {line_graph}")
    print(f"Wrote: {summary_graph}")
    print(f"Wrote: {html_path}")
    print("Summary:")
    for row in summary:
        mean = row["mean_visible_offset_mm"]
        mean_text = f"{float(mean):.2f}" if mean != "" else "-"
        print(
            f"  {row['method']}: pass {row['pass_count_20mm']}/{row['total']} "
            f"({float(row['pass_rate_20mm_percent']):.1f}%), "
            f"not_visible={row['laser_not_visible']}, mean_visible={mean_text} mm"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
