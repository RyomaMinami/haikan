"""
Merge a partial angle rescan into an existing angle scan CSV.

Example:
    python merge_angle_rescan.py ^
      --base auto_scan_1mm.csv ^
      --rescan auto_scan_1mm_45_55_retry.csv ^
      --replace-angles 45,50,55 ^
      --output auto_scan_1mm_merged.csv

The script removes the specified angles from the base CSV, appends rows for
those angles from the rescan CSV, then sorts by angle_group/angle/target.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Optional


def parse_float(text: str) -> Optional[float]:
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def angle_key(value: float) -> int:
    return round(value * 1000)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)


def sort_key(row: dict[str, str]) -> tuple[float, float, float, float]:
    angle = parse_float(row.get("angle_deg", "")) or 0.0
    target = parse_float(row.get("target_delta_mm", "")) or 0.0
    progress = parse_float(row.get("dl50_progress_mm", "")) or target
    elapsed = parse_float(row.get("elapsed_s", "")) or 0.0
    return angle, target, progress, elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description="Replace selected angles in an auto scan CSV with rescan data.")
    parser.add_argument("--base", required=True)
    parser.add_argument("--rescan", required=True)
    parser.add_argument("--replace-angles", required=True, help="Comma-separated angles, e.g. 45,50,55")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    base_path = Path(args.base)
    rescan_path = Path(args.rescan)
    output_path = Path(args.output)

    base_fields, base_rows = read_csv(base_path)
    rescan_fields, rescan_rows = read_csv(rescan_path)
    if not base_fields:
        print(f"No header in base CSV: {base_path}")
        return 1
    if not rescan_fields:
        print(f"No header in rescan CSV: {rescan_path}")
        return 1

    fieldnames = list(base_fields)
    for name in rescan_fields:
        if name not in fieldnames:
            fieldnames.append(name)

    replace_angles = {
        angle_key(float(text.strip()))
        for text in args.replace_angles.split(",")
        if text.strip()
    }

    kept_base = []
    removed_base = 0
    for row in base_rows:
        angle = parse_float(row.get("angle_deg", ""))
        if angle is not None and angle_key(angle) in replace_angles:
            removed_base += 1
        else:
            kept_base.append(row)

    added_rescan = []
    ignored_rescan = 0
    for row in rescan_rows:
        angle = parse_float(row.get("angle_deg", ""))
        if angle is not None and angle_key(angle) in replace_angles:
            added_rescan.append(row)
        else:
            ignored_rescan += 1

    merged = kept_base + added_rescan
    merged.sort(key=sort_key)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)

    print(f"Base rows: {len(base_rows)}")
    print(f"Removed base rows: {removed_base}")
    print(f"Rescan rows: {len(rescan_rows)}")
    print(f"Added rescan rows: {len(added_rescan)}")
    print(f"Ignored rescan rows: {ignored_rescan}")
    print(f"Merged rows: {len(merged)}")
    print(f"Output: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
