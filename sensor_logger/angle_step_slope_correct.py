"""
Apply linear slope/drift correction to angle step log data.

Assumption:
    The measurement is a straight-line motion, so the first and last LK-G values
    for each angle group should be equal. Any first-to-last LK-G difference is
    treated as a linear tilt/drift error along DL50 distance.

Correction per angle group and LK channel:
    drift(x) = (x - x0) / (x1 - x0) * (y1 - y0)
    y_corrected = y - drift(x)

This keeps the first value unchanged and makes the last corrected value equal
to the first corrected value.

Examples:
    python angle_step_slope_correct.py --input angle_step_log_clean.csv
    python angle_step_slope_correct.py --input angle_step_log_5mm_clean.csv --pivot
"""

from __future__ import annotations

import argparse
import csv
from collections import OrderedDict
from pathlib import Path
from typing import Optional


BASE_COLUMNS = [
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

CORRECTED_COLUMNS = BASE_COLUMNS + [
    "lk_out1_corrected_mm",
    "lk_out2_corrected_mm",
    "lk_out1_drift_mm",
    "lk_out2_drift_mm",
]


def parse_float(text: str) -> Optional[float]:
    if text is None or text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def format_optional(value: Optional[float]) -> str:
    return "" if value is None else f"{value:.6f}"


def group_key(row: dict[str, str]) -> tuple[str, str]:
    return (row.get("angle_group", ""), row.get("angle_deg", ""))


def x_value(row: dict[str, str]) -> Optional[float]:
    # Prefer measured DL50 delta because the target index may have skipped when
    # the mechanism moved faster than the logger period.
    measured = parse_float(row.get("dl50_delta_mm", ""))
    if measured is not None:
        return measured
    return parse_float(row.get("target_delta_mm", ""))


def correct_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: OrderedDict[tuple[str, str], list[dict[str, str]]] = OrderedDict()
    for row in rows:
        groups.setdefault(group_key(row), []).append(row)

    corrected: list[dict[str, str]] = []

    for _, group_rows in groups.items():
        valid_rows = [
            row
            for row in group_rows
            if x_value(row) is not None
            and parse_float(row.get("lk_out1_mm", "")) is not None
            and parse_float(row.get("lk_out2_mm", "")) is not None
        ]

        if len(valid_rows) < 2:
            for row in group_rows:
                new_row = dict(row)
                new_row.update(
                    {
                        "lk_out1_corrected_mm": row.get("lk_out1_mm", ""),
                        "lk_out2_corrected_mm": row.get("lk_out2_mm", ""),
                        "lk_out1_drift_mm": "",
                        "lk_out2_drift_mm": "",
                    }
                )
                corrected.append(new_row)
            continue

        first = valid_rows[0]
        last = valid_rows[-1]
        x0 = x_value(first)
        x1 = x_value(last)
        y10 = parse_float(first.get("lk_out1_mm", ""))
        y11 = parse_float(last.get("lk_out1_mm", ""))
        y20 = parse_float(first.get("lk_out2_mm", ""))
        y21 = parse_float(last.get("lk_out2_mm", ""))

        assert x0 is not None and x1 is not None
        assert y10 is not None and y11 is not None and y20 is not None and y21 is not None

        span = x1 - x0
        if span == 0:
            span = 1.0

        for row in group_rows:
            x = x_value(row)
            y1 = parse_float(row.get("lk_out1_mm", ""))
            y2 = parse_float(row.get("lk_out2_mm", ""))
            new_row = dict(row)

            if x is None or y1 is None or y2 is None:
                new_row.update(
                    {
                        "lk_out1_corrected_mm": "",
                        "lk_out2_corrected_mm": "",
                        "lk_out1_drift_mm": "",
                        "lk_out2_drift_mm": "",
                    }
                )
            else:
                ratio = (x - x0) / span
                drift1 = ratio * (y11 - y10)
                drift2 = ratio * (y21 - y20)
                new_row.update(
                    {
                        "lk_out1_corrected_mm": format_optional(y1 - drift1),
                        "lk_out2_corrected_mm": format_optional(y2 - drift2),
                        "lk_out1_drift_mm": format_optional(drift1),
                        "lk_out2_drift_mm": format_optional(drift2),
                    }
                )

            corrected.append(new_row)

    return corrected


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


def write_corrected(rows: list[dict[str, str]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CORRECTED_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_pivot(rows: list[dict[str, str]], path: Path) -> None:
    angles = list(OrderedDict((row.get("angle_deg", ""), None) for row in rows).keys())
    targets = list(OrderedDict((target_key(row), None) for row in rows).keys())
    by_target_angle = {(target_key(row), row.get("angle_deg", "")): row for row in rows}

    columns = ["target_delta_mm"]
    for angle in angles:
        prefix = angle_label(angle)
        columns.extend(
            [
                f"{prefix}_lk_out1_corrected_mm",
                f"{prefix}_lk_out2_corrected_mm",
                f"{prefix}_lk_out1_mm",
                f"{prefix}_lk_out2_mm",
                f"{prefix}_dl50_hi_mm",
                f"{prefix}_dl50_delta_mm",
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
                out[f"{prefix}_lk_out1_corrected_mm"] = row.get("lk_out1_corrected_mm", "")
                out[f"{prefix}_lk_out2_corrected_mm"] = row.get("lk_out2_corrected_mm", "")
                out[f"{prefix}_lk_out1_mm"] = row.get("lk_out1_mm", "")
                out[f"{prefix}_lk_out2_mm"] = row.get("lk_out2_mm", "")
                out[f"{prefix}_dl50_hi_mm"] = row.get("dl50_hi_mm", "")
                out[f"{prefix}_dl50_delta_mm"] = row.get("dl50_delta_mm", "")
            writer.writerow(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply LK-G linear slope correction.")
    parser.add_argument("--input", default="angle_step_log_clean.csv")
    parser.add_argument("--output", default=None)
    parser.add_argument("--pivot", action="store_true", help="Also write a pivoted wide CSV.")
    parser.add_argument("--pivot-output", default=None)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Input CSV not found: {input_path}")
        return 1

    output_path = (
        Path(args.output)
        if args.output
        else input_path.with_name(f"{input_path.stem}_corrected{input_path.suffix}")
    )
    pivot_path = (
        Path(args.pivot_output)
        if args.pivot_output
        else input_path.with_name(f"{input_path.stem}_corrected_pivot{input_path.suffix}")
    )

    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    corrected = correct_rows(rows)
    write_corrected(corrected, output_path)
    print(f"Input rows: {len(rows)}")
    print(f"Corrected CSV: {output_path.resolve()}")

    if args.pivot:
        write_pivot(corrected, pivot_path)
        print(f"Corrected pivot CSV: {pivot_path.resolve()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
