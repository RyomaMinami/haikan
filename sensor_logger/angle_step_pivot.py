"""
Clean and pivot angle_step_trigger_logger CSV.

This keeps the first row for each same angle + same DL50 distance, then creates
a wide table where each angle has its own columns.

Outputs:
    <input>_clean.csv  long-format cleaned data
    <input>_pivot.csv  wide table grouped by target_delta_mm

Example:
    python angle_step_pivot.py --input angle_step_log.csv
"""

from __future__ import annotations

import argparse
import csv
from collections import OrderedDict
from pathlib import Path
from typing import Optional


LONG_COLUMNS = [
    "pc_time",
    "elapsed_s",
    "angle_group",
    "angle_deg",
    "servo_position",
    "trigger_index",
    "target_delta_mm",
    "dl50_initial_mm",
    "dl50_hi_mm",
    "dl50_delta_mm",
    "dl50_raw",
    "lk_out1_mm",
    "lk_out2_mm",
    "lk_out1_status",
    "lk_out2_status",
]


def parse_float(text: str) -> Optional[float]:
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def angle_label(angle_text: str) -> str:
    value = parse_float(angle_text)
    if value is None:
        return "unknown"
    label = f"{value:g}".replace("-", "m").replace(".", "p")
    return f"angle_{label}deg"


def target_key(row: dict[str, str]) -> str:
    target = parse_float(row.get("target_delta_mm", ""))
    if target is not None:
        return f"{target:.3f}"
    return row.get("trigger_index", "")


def clean_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen_angle_dl50: set[tuple[str, int]] = set()
    seen_angle_target: set[tuple[str, str]] = set()
    cleaned: list[dict[str, str]] = []

    for row in rows:
        angle = row.get("angle_deg", "")
        dl50 = parse_float(row.get("dl50_hi_mm", ""))
        if dl50 is None:
            continue

        # Millimetre values are logged with 0.001 mm formatting. Rounding to
        # micrometres makes duplicate detection stable for CSV text variants.
        dl50_key = round(dl50 * 1000)
        angle_dl50_key = (angle, dl50_key)
        if angle_dl50_key in seen_angle_dl50:
            continue
        seen_angle_dl50.add(angle_dl50_key)

        angle_target_key = (angle, target_key(row))
        if angle_target_key in seen_angle_target:
            continue
        seen_angle_target.add(angle_target_key)

        cleaned.append(row)

    return cleaned


def write_clean(rows: list[dict[str, str]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=LONG_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_pivot(rows: list[dict[str, str]], path: Path) -> None:
    angles = list(OrderedDict((row.get("angle_deg", ""), None) for row in rows).keys())
    targets = list(OrderedDict((target_key(row), None) for row in rows).keys())

    by_target_angle: dict[tuple[str, str], dict[str, str]] = {}
    for row in rows:
        key = (target_key(row), row.get("angle_deg", ""))
        by_target_angle.setdefault(key, row)

    columns = ["target_delta_mm"]
    for angle in angles:
        prefix = angle_label(angle)
        columns.extend(
            [
                f"{prefix}_lk_out1_mm",
                f"{prefix}_lk_out2_mm",
                f"{prefix}_dl50_hi_mm",
                f"{prefix}_dl50_delta_mm",
                f"{prefix}_pc_time",
            ]
        )

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for target in targets:
            out = {"target_delta_mm": target}
            for angle in angles:
                row = by_target_angle.get((target, angle))
                if row is None:
                    continue
                prefix = angle_label(angle)
                out[f"{prefix}_lk_out1_mm"] = row.get("lk_out1_mm", "")
                out[f"{prefix}_lk_out2_mm"] = row.get("lk_out2_mm", "")
                out[f"{prefix}_dl50_hi_mm"] = row.get("dl50_hi_mm", "")
                out[f"{prefix}_dl50_delta_mm"] = row.get("dl50_delta_mm", "")
                out[f"{prefix}_pc_time"] = row.get("pc_time", "")
            writer.writerow(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean and pivot angle step log CSV.")
    parser.add_argument("--input", default="angle_step_log.csv")
    parser.add_argument("--clean-output", default=None)
    parser.add_argument("--pivot-output", default=None)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input CSV not found: {input_path}")
        return 1

    clean_path = (
        Path(args.clean_output)
        if args.clean_output
        else input_path.with_name(f"{input_path.stem}_clean{input_path.suffix}")
    )
    pivot_path = (
        Path(args.pivot_output)
        if args.pivot_output
        else input_path.with_name(f"{input_path.stem}_pivot{input_path.suffix}")
    )

    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    cleaned = clean_rows(rows)
    write_clean(cleaned, clean_path)
    write_pivot(cleaned, pivot_path)

    print(f"Input rows: {len(rows)}")
    print(f"Clean rows: {len(cleaned)}")
    print(f"Clean CSV: {clean_path.resolve()}")
    print(f"Pivot CSV: {pivot_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
