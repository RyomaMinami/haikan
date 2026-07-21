#!/usr/bin/env python3
"""
Run a fresh scan, estimate dent centers for downsampled resolution conditions,
then move to each estimated center using one shared start origin.

Use this when the physical scan start may differ from older experiments. The
sequence is:

  1. Run a new automatic -115..115 deg scan.
  2. Run the resolution ablation study on that exact CSV.
  3. Ask the operator to return/confirm the same physical start point.
  4. Move through each estimated center in the results CSV.

The move sequence records DL50 start only once, so every condition is evaluated
from the same origin.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent


def run_cmd(cmd: list[str]) -> None:
    print()
    print("[CMD] " + " ".join(cmd))
    subprocess.run(cmd, cwd=SCRIPT_DIR, check=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fresh scan -> resolution center estimation -> sequential center movement."
    )
    parser.add_argument("--csv", default="pipe154_rescan_for_resolution_move.csv")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--hole-diameter-mm", type=float, default=154.0)
    parser.add_argument("--pipe-radius-mm", type=float, default=120.0)
    parser.add_argument("--base-radius-mm", type=float, default=120.0)

    parser.add_argument("--dl50-port", default="COM10")
    parser.add_argument("--servo-port", default="COM8")
    parser.add_argument("--laser-id", type=int, default=5)
    parser.add_argument("--wheel-id", type=int, default=4)
    parser.add_argument("--angle-start", type=float, default=-115.0)
    parser.add_argument("--angle-end", type=float, default=115.0)
    parser.add_argument("--angle-step", type=float, default=5.0)
    parser.add_argument("--step-mm", type=float, default=1.0)
    parser.add_argument("--wheel-forward-speed", type=int, default=7350)
    parser.add_argument("--wheel-return-speed", type=int, default=9500)
    parser.add_argument("--fixed-start-mm", type=float, default=180.0)
    parser.add_argument("--fixed-end-mm", type=float, default=900.0)
    parser.add_argument("--record-return", action="store_true", help="Also record while moving end -> start.")

    parser.add_argument("--study-angle-steps", default="5,10,15")
    parser.add_argument("--study-x-steps", default="1,2,5,10")
    parser.add_argument(
        "--conditions",
        help="Comma-separated conditions to move to. Default is all rows, e.g. a5_x1,a5_x2,...",
    )
    parser.add_argument("--skip-scan", action="store_true", help="Use an existing CSV and only analyze/move.")
    parser.add_argument("--skip-analysis", action="store_true", help="Use an existing resolution results CSV.")
    parser.add_argument(
        "--execute-move",
        action="store_true",
        help="Actually open COM ports and move. Without this, only lists targets.",
    )
    parser.add_argument("--move-tolerance-mm", type=float, default=1.0)
    parser.add_argument("--move-forward-speed", type=int, default=7350)
    parser.add_argument("--move-reverse-speed", type=int, default=7650)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    csv_path = Path(args.csv)
    if not csv_path.is_absolute():
        csv_path = SCRIPT_DIR / csv_path

    output_dir = Path(args.output_dir) if args.output_dir else SCRIPT_DIR / f"{csv_path.stem}_resolution_study"
    if not output_dir.is_absolute():
        output_dir = SCRIPT_DIR / output_dir
    results_csv = output_dir / f"{csv_path.stem}_resolution_results.csv"

    print("============================================================")
    print("[目的]")
    print("新しく測定したCSVから各分解能条件の中心位置を推定し、")
    print("その測定時の始点を共通基準として、各中心位置へ順番に移動します。")
    print("============================================================")
    print(f"[SCAN CSV] {csv_path}")
    print(f"[STUDY DIR] {output_dir}")
    print(f"[RESULTS]  {results_csv}")

    if not args.skip_scan:
        print()
        print("============================================================")
        print("[STEP 1] 新しい測定を開始します")
        print("PowerShellに表示される指示に従って、始点と終点を合わせてください。")
        print("測定後、プログラムは各角度で始点に戻りながらCSVを作成します。")
        print("============================================================")
        scan_return_speed = (
            7500 + (7500 - args.wheel_forward_speed)
            if args.record_return
            else args.wheel_return_speed
        )
        if args.record_return:
            scan_cmd = [
                sys.executable,
                "auto_angle_wheel_scan_logger.py",
                "--dl50-port",
                args.dl50_port,
                "--servo-port",
                args.servo_port,
                "--laser-id",
                str(args.laser_id),
                "--wheel-id",
                str(args.wheel_id),
                "--angle-start",
                str(args.angle_start),
                "--angle-end",
                str(args.angle_end),
                "--angle-step",
                str(args.angle_step),
                "--step-mm",
                str(args.step_mm),
                "--wheel-forward-speed",
                str(args.wheel_forward_speed),
                "--wheel-return-speed",
                str(scan_return_speed),
                "--fixed-start-mm",
                str(args.fixed_start_mm),
                "--fixed-end-mm",
                str(args.fixed_end_mm),
                "--csv",
                str(csv_path),
            ]
            scan_cmd.insert(-2, "--record-return")
        else:
            scan_cmd = [
                sys.executable,
                "auto_angle_wheel_scan_logger.py",
                "--dl50-port",
                args.dl50_port,
                "--servo-port",
                args.servo_port,
                "--laser-id",
                str(args.laser_id),
                "--wheel-id",
                str(args.wheel_id),
                "--angle-start",
                str(args.angle_start),
                "--angle-end",
                str(args.angle_end),
                "--angle-step",
                str(args.angle_step),
                "--step-mm",
                str(args.step_mm),
                "--wheel-forward-speed",
                str(args.wheel_forward_speed),
                "--wheel-return-speed",
                str(scan_return_speed),
                "--fixed-start-mm",
                str(args.fixed_start_mm),
                "--fixed-end-mm",
                str(args.fixed_end_mm),
                "--csv",
                str(csv_path),
            ]
        run_cmd(scan_cmd)

    if not args.skip_analysis:
        print()
        print("============================================================")
        print("[STEP 2] 分解能ごとの中心推定を作成します")
        print("a5_x1, a5_x2, ... a15_x10 の中心位置を同じCSVから計算します。")
        print("============================================================")
        run_cmd(
            [
                sys.executable,
                "resolution_ablation_study.py",
                "--input",
                str(csv_path),
                "--output-dir",
                str(output_dir),
                "--angle-steps",
                args.study_angle_steps,
                "--x-steps",
                args.study_x_steps,
                "--hole-diameter-mm",
                str(args.hole_diameter_mm),
                "--pipe-radius-mm",
                str(args.pipe_radius_mm),
                "--base-radius-mm",
                str(args.base_radius_mm),
            ]
        )

    print()
    print("============================================================")
    print("[STEP 3] 推定中心への移動")
    print("ここから先は、DL50の始点値を一回だけ記録します。")
    print("測定時と同じ物理的な始点へ装置を戻してから、Enterを押してください。")
    print("その始点を使って、全条件の中心位置へ順番に移動します。")
    print("============================================================")

    move_cmd = [
        sys.executable,
        "move_resolution_centers_sequence.py",
        "--results",
        str(results_csv),
        "--dl50-port",
        args.dl50_port,
        "--servo-port",
        args.servo_port,
        "--laser-id",
        str(args.laser_id),
        "--wheel-id",
        str(args.wheel_id),
        "--wheel-forward-speed",
        str(args.move_forward_speed),
        "--wheel-reverse-speed",
        str(args.move_reverse_speed),
        "--tolerance-mm",
        str(args.move_tolerance_mm),
    ]
    if args.conditions:
        move_cmd += ["--conditions", args.conditions]
    if args.execute_move:
        move_cmd.append("--execute")
    else:
        print("[DRY RUN] 移動はまだ実行しません。実際に動かすには --execute-move を付けてください。")

    run_cmd(move_cmd)

    print()
    print("[DONE] 完了しました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
