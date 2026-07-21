#!/usr/bin/env python3
"""
Map sequential detection-method photos to movement targets.

The movement program visits the rows in the target CSV order. If fewer photos
than targets exist, this script keeps the photo sequence and inserts missing
photo slots at the largest timestamp gaps. Very long gaps can be ignored as
operator pauses.
"""

from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


DEFAULT_TARGETS = Path("pipe154_detection_method_photo_targets_all.csv")
DEFAULT_PHOTO_DIR = Path(r"C:\Users\minam\Downloads\Photos-3-001 (1)")
DEFAULT_OUTPUT_DIR = Path("pipe154_detection_method_photo_mapping_20260609")


def parse_photo_time(path: Path) -> datetime:
    match = re.search(r"PXL_(\d{8})_(\d{9})", path.name)
    if not match:
        raise ValueError(f"Could not parse timestamp from photo name: {path.name}")
    date, time = match.groups()
    return datetime.strptime(date + time[:6] + time[6:], "%Y%m%d%H%M%S%f")


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


def choose_missing_after_indices(
    photos: list[Path],
    missing_count: int,
    ignore_gap_over_s: float,
) -> tuple[set[int], list[dict[str, object]]]:
    timed = [(photo, parse_photo_time(photo)) for photo in photos]
    gaps: list[dict[str, object]] = []
    for i in range(len(timed) - 1):
        gap = (timed[i + 1][1] - timed[i][1]).total_seconds()
        gaps.append(
            {
                "after_photo_index": i + 1,
                "gap_s": gap,
                "before_photo": timed[i][0].name,
                "after_photo": timed[i + 1][0].name,
                "ignored_as_pause": gap > ignore_gap_over_s,
            }
        )

    usable = [gap for gap in gaps if not gap["ignored_as_pause"]]
    chosen = sorted(usable, key=lambda row: float(row["gap_s"]), reverse=True)[:missing_count]
    return {int(row["after_photo_index"]) for row in chosen}, gaps


def build_mapping(
    targets: list[dict[str, str]],
    photos: list[Path],
    missing_after: set[int],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    target_index = 0
    photo_index = 0

    while target_index < len(targets):
        if photo_index > 0 and photo_index in missing_after:
            target = targets[target_index]
            rows.append(
                {
                    "target_index": target_index + 1,
                    "photo_index": "",
                    "status": "missing_photo_gap_inferred",
                    "photo_name": "",
                    "photo_path": "",
                    **target,
                }
            )
            target_index += 1
            missing_after.remove(photo_index)
            continue

        if photo_index >= len(photos):
            target = targets[target_index]
            rows.append(
                {
                    "target_index": target_index + 1,
                    "photo_index": "",
                    "status": "missing_photo_at_end",
                    "photo_name": "",
                    "photo_path": "",
                    **target,
                }
            )
            target_index += 1
            continue

        target = targets[target_index]
        photo = photos[photo_index]
        rows.append(
            {
                "target_index": target_index + 1,
                "photo_index": photo_index + 1,
                "status": "mapped",
                "photo_name": photo.name,
                "photo_path": str(photo),
                **target,
            }
        )
        target_index += 1
        photo_index += 1

    return rows


def write_html(path: Path, mapping_rows: list[dict[str, object]], gap_rows: list[dict[str, object]]) -> None:
    missing_rows = [row for row in mapping_rows if str(row["status"]).startswith("missing")]
    missing_html = "\n".join(
        "<tr>"
        f"<td>{row['target_index']}</td>"
        f"<td>{row.get('condition', '')}</td>"
        f"<td>{row.get('method', '')}</td>"
        f"<td>{row.get('center_x_zero_mm', '')}</td>"
        f"<td>{row.get('center_angle_deg', '')}</td>"
        f"<td>{row['status']}</td>"
        "</tr>"
        for row in missing_rows
    )
    gap_html = "\n".join(
        "<tr>"
        f"<td>{row['after_photo_index']}</td>"
        f"<td>{float(row['gap_s']):.3f}</td>"
        f"<td>{row['before_photo']}</td>"
        f"<td>{row['after_photo']}</td>"
        f"<td>{'yes' if row['ignored_as_pause'] else ''}</td>"
        "</tr>"
        for row in sorted(gap_rows, key=lambda r: float(r["gap_s"]), reverse=True)[:25]
    )
    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Detection Method Photo Mapping</title>
  <style>
    body {{ font-family: Arial, "Meiryo", sans-serif; margin: 24px; color: #202124; }}
    table {{ border-collapse: collapse; width: 100%; margin: 16px 0 28px; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: right; }}
    th:nth-child(2), th:nth-child(3), td:nth-child(2), td:nth-child(3),
    th:nth-child(4), td:nth-child(4) {{ text-align: left; }}
    th {{ background: #f6f8fa; }}
    .note {{ background: #fff8c5; border: 1px solid #d4a72c; padding: 10px 12px; border-radius: 6px; line-height: 1.55; }}
  </style>
</head>
<body>
  <h1>Detection Method Photo Mapping</h1>
  <p class="note">
    ターゲット144点に対して写真141枚でした。写真は撮影時刻順に並べ、長すぎる空き時間は作業中断として無視し、
    それ以外の大きな時間ギャップ3か所に欠番写真を挿入しました。レーザーが写っていない写真は欠番扱いせず、
    窪み外へ出た測定結果として対応表に残します。
  </p>
  <h2>推定された欠番ターゲット</h2>
  <table>
    <thead><tr><th>target index</th><th>condition</th><th>method</th><th>x mm</th><th>angle deg</th><th>status</th></tr></thead>
    <tbody>{missing_html}</tbody>
  </table>
  <h2>撮影時刻ギャップ 上位25件</h2>
  <table>
    <thead><tr><th>after photo index</th><th>gap s</th><th>before</th><th>after</th><th>ignored as pause</th></tr></thead>
    <tbody>{gap_html}</tbody>
  </table>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Map 2026-06-09 photos to detection-method target rows.")
    parser.add_argument("--targets", type=Path, default=DEFAULT_TARGETS)
    parser.add_argument("--photo-dir", type=Path, default=DEFAULT_PHOTO_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ignore-gap-over-s", type=float, default=60.0)
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    if not args.targets.is_absolute():
        args.targets = script_dir / args.targets
    if not args.output_dir.is_absolute():
        args.output_dir = script_dir / args.output_dir

    targets = read_csv(args.targets)
    photos = sorted(args.photo_dir.glob("*.jpg"), key=lambda p: p.name)
    missing_count = len(targets) - len(photos)
    if missing_count < 0:
        raise SystemExit(f"More photos than targets: photos={len(photos)} targets={len(targets)}")

    missing_after, gaps = choose_missing_after_indices(photos, missing_count, args.ignore_gap_over_s)
    mapping = build_mapping(targets, photos, set(missing_after))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    mapping_csv = args.output_dir / "photo_target_mapping_gap_inferred.csv"
    gaps_csv = args.output_dir / "photo_time_gaps.csv"
    html_path = args.output_dir / "photo_target_mapping_gap_inferred.html"
    write_csv(mapping_csv, mapping)
    write_csv(gaps_csv, gaps)
    write_html(html_path, mapping, gaps)

    print(f"targets={len(targets)} photos={len(photos)} missing={missing_count}")
    print("missing-after photo indices:", ", ".join(str(i) for i in sorted(missing_after)) or "(none)")
    print("missing target rows:")
    for row in mapping:
        if str(row["status"]).startswith("missing"):
            print(f"  {row['target_index']}: {row.get('condition')} / {row.get('method')}")
    print(f"Wrote: {mapping_csv}")
    print(f"Wrote: {gaps_csv}")
    print(f"Wrote: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
