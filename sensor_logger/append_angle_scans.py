"""
Append multiple angle scan CSV files into one CSV.

Use this after separately measuring extra angle ranges.

Example:
    python append_angle_scans.py ^
      --inputs auto_scan_1mm_merged.csv,auto_scan_1mm_m115_m95.csv,auto_scan_1mm_95_115_fixed.csv ^
      --output auto_scan_1mm_full_m115_115.csv

If duplicate angles exist, the later input file wins for those angles.
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
    parser = argparse.ArgumentParser(description="Append angle scan CSVs. Later files replace duplicate angles.")
    parser.add_argument("--inputs", required=True, help="Comma-separated CSV files in priority order.")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    input_paths = [Path(text.strip()) for text in args.inputs.split(",") if text.strip()]
    if not input_paths:
        print("No input files.")
        return 1

    fieldnames: list[str] = []
    rows_by_angle: dict[int, list[dict[str, str]]] = {}
    input_counts: list[tuple[Path, int, list[float]]] = []

    for path in input_paths:
        fields, rows = read_csv(path)
        if not fields:
            print(f"No header: {path}")
            return 1
        for name in fields:
            if name not in fieldnames:
                fieldnames.append(name)

        angles: set[float] = set()
        grouped: dict[int, list[dict[str, str]]] = {}
        for row in rows:
            angle = parse_float(row.get("angle_deg", ""))
            if angle is None:
                continue
            key = angle_key(angle)
            angles.add(angle)
            grouped.setdefault(key, []).append(row)

        # Later files replace the complete angle group from earlier files.
        for key, angle_rows in grouped.items():
            rows_by_angle[key] = angle_rows

        input_counts.append((path, len(rows), sorted(angles)))

    merged: list[dict[str, str]] = []
    for key in sorted(rows_by_angle):
        merged.extend(sorted(rows_by_angle[key], key=sort_key))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)

    for path, count, angles in input_counts:
        angle_text = ", ".join(f"{a:g}" for a in angles)
        print(f"Input: {path} rows={count} angles=[{angle_text}]")
    final_angles = sorted(rows_by_angle)
    print(f"Merged rows: {len(merged)}")
    print(f"Merged angle count: {len(final_angles)}")
    print(f"Output: {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
