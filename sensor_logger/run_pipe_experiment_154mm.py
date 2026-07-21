"""
Run the next pipe experiment workflow.

Workflow:
    1. Create realtime visualization HTML for the measurement CSV.
    2. Start a local HTTP server if needed.
    3. Run automatic -115..115 deg scan and write CSV.
    4. Immediately run visualization, axis correction, edge detection,
       circular edge matching, dent region marking, and combined HTML.
    5. Print the detected center.
    6. Optionally move to the detected center after user confirmation.

Close ICS Manager before running this script.
"""

from __future__ import annotations

import argparse
import csv
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent


def run_cmd(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    print()
    print("[CMD] " + " ".join(args))
    return subprocess.run(args, cwd=SCRIPT_DIR, check=check)


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_http_server(port: int) -> Optional[subprocess.Popen]:
    if is_port_open(port):
        print(f"[HTTP] Already running: http://127.0.0.1:{port}/")
        return None
    print(f"[HTTP] Starting server: http://127.0.0.1:{port}/")
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=SCRIPT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    return proc


def read_summary(path: Path) -> dict[str, str]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    return rows[0] if rows else {}


def read_detected_center(dent_region_csv: Path) -> tuple[Optional[float], Optional[float]]:
    center_axis = None
    center_angle = None
    axis_min = None
    with dent_region_csv.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if center_axis is None and row.get("dent_center_axis_s_mm"):
                center_axis = float(row["dent_center_axis_s_mm"])
            if center_angle is None and row.get("dent_center_angle_deg"):
                center_angle = float(row["dent_center_angle_deg"])
            if axis_min is None and row.get("coordinate_axis_min_mm"):
                axis_min = float(row["coordinate_axis_min_mm"])
            if center_axis is not None and center_angle is not None and axis_min is not None:
                break
    if center_axis is None or center_angle is None or axis_min is None:
        return None, None
    return center_axis - axis_min, center_angle


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Full -115..115 pipe experiment workflow for 154 mm dent.")
    parser.add_argument("--csv", default="pipe154_auto_scan_m115_115.csv")
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
    parser.add_argument("--wheel-return-speed", type=int, default=11500)
    parser.add_argument("--http-port", type=int, default=8765)
    parser.add_argument("--no-open-browser", action="store_true")
    parser.add_argument("--skip-scan", action="store_true", help="Use existing CSV and only run analysis.")
    parser.add_argument("--move-after-analysis", action="store_true")
    parser.add_argument("--move-wheel-forward-speed", type=int, default=7350)
    parser.add_argument("--move-wheel-reverse-speed", type=int, default=7650)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    csv_path = Path(args.csv)
    stem = csv_path.stem
    realtime_html = csv_path.with_name(f"{stem}_realtime.html")

    http_proc = ensure_http_server(args.http_port)
    try:
        run_cmd(
            [
                sys.executable,
                "create_rviz_like_realtime_view.py",
                "--csv",
                str(csv_path),
                "--output-html",
                str(realtime_html),
                "--base-radius-mm",
                str(args.base_radius_mm),
                "--invert-lk",
                "--refresh-ms",
                "700",
            ]
        )
        realtime_url = f"http://127.0.0.1:{args.http_port}/{realtime_html.name}"
        print(f"[VIEW] Realtime: {realtime_url}")
        if not args.no_open_browser:
            webbrowser.open(realtime_url)

        if not args.skip_scan:
            print()
            print("============================================================")
            print("[STEP 1] 測定を開始します。PowerShellの指示に従って始点・終点を合わせてください。")
            print("[STEP 1] 測定中はリアルタイムHTMLがCSVを読み続けます。")
            print("============================================================")
            run_cmd(
                [
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
                    str(args.wheel_return_speed),
                    "--csv",
                    str(csv_path),
                ]
            )

        print()
        print("============================================================")
        print("[STEP 2] 測定完了。穴径154mmとして解析を一気に実行します。")
        print("============================================================")
        point_cloud = csv_path.with_name(f"{stem}_point_cloud.csv")
        axis_csv = csv_path.with_name(f"{stem}_point_cloud_axis_corrected.csv")
        edge_grid = csv_path.with_name(f"{stem}_axis_edge_grid_p97.csv")
        edge_csv = csv_path.with_name(f"{stem}_axis_edges_p97.csv")
        edge_html = csv_path.with_name(f"{stem}_axis_edges_p97.html")
        summary_csv = csv_path.with_name(f"{stem}_circular_edge_match_summary.csv")
        boundary_csv = csv_path.with_name(f"{stem}_circular_edge_boundary.csv")
        match_html = csv_path.with_name(f"{stem}_circular_edge_match.html")
        dent_csv = csv_path.with_name(f"{stem}_detected_dent_region.csv")
        dent_html = csv_path.with_name(f"{stem}_detected_dent_region.html")
        combined_html = csv_path.with_name(f"{stem}_combined_analysis.html")

        run_cmd(
            [
                sys.executable,
                "pipe_surface_visualizer.py",
                "--input",
                str(csv_path),
                "--base-radius-mm",
                str(args.base_radius_mm),
                "--invert-lk",
            ]
        )
        run_cmd(
            [
                sys.executable,
                "axis_correct_point_cloud.py",
                "--input",
                str(point_cloud),
                "--reference-radius-mm",
                str(args.pipe_radius_mm),
                "--section-bin-mm",
                "20",
                "--min-section-points",
                "80",
            ]
        )
        run_cmd(
            [
                sys.executable,
                "axis_edge_detect.py",
                "--input",
                str(axis_csv),
                "--radius-mm",
                str(args.pipe_radius_mm),
                "--axis-step-mm",
                "1",
                "--sigma-axis-mm",
                "0.8",
                "--sigma-angle-steps",
                "1.6",
                "--theta-gradient-weight",
                "0.25",
                "--edge-percentile",
                "97",
                "--outward-gate-percentile",
                "45",
                "--output-edges",
                str(edge_csv),
                "--output-grid",
                str(edge_grid),
                "--output-html",
                str(edge_html),
            ]
        )
        run_cmd(
            [
                sys.executable,
                "axis_edge_template_match.py",
                "--input-grid",
                str(edge_grid),
                "--hole-diameter-mm",
                str(args.hole_diameter_mm),
                "--pipe-radius-mm",
                str(args.pipe_radius_mm),
                "--base-radius-mm",
                str(args.base_radius_mm),
                "--edge-band-mm",
                "5",
                "--angular-edge-weight",
                "0.25",
                "--output-summary",
                str(summary_csv),
                "--output-boundary",
                str(boundary_csv),
                "--output-html",
                str(match_html),
            ]
        )
        run_cmd(
            [
                sys.executable,
                "axis_dent_region_from_match.py",
                "--points",
                str(axis_csv),
                "--summary",
                str(summary_csv),
                "--edge-band-mm",
                "5",
                "--base-radius-mm",
                str(args.base_radius_mm),
                "--output-csv",
                str(dent_csv),
                "--output-html",
                str(dent_html),
            ]
        )
        run_cmd(
            [
                sys.executable,
                "combined_pipe_analysis_view.py",
                "--raw-point-cloud",
                str(point_cloud),
                "--axis-point-cloud",
                str(axis_csv),
                "--edge-grid",
                str(edge_grid),
                "--boundary",
                str(boundary_csv),
                "--summary",
                str(summary_csv),
                "--dent-region",
                str(dent_csv),
                "--output",
                str(combined_html),
            ]
        )

        combined_url = f"http://127.0.0.1:{args.http_port}/{combined_html.name}"
        center_x, center_angle = read_detected_center(dent_csv)
        print()
        print("============================================================")
        print("[RESULT] 解析完了")
        print(f"[RESULT] Combined HTML: {combined_url}")
        if center_x is not None and center_angle is not None:
            print(f"[RESULT] 検出中心: x={center_x:.3f} mm, angle={center_angle:.3f} deg")
        print("============================================================")
        if not args.no_open_browser:
            webbrowser.open(combined_url)

        if args.move_after_analysis:
            print()
            print("[MOVE] 統合HTMLで結果を確認してください。")
            input("[MOVE] 中心位置へ移動してよければ Enter を押してください。中止する場合は Ctrl+C > ")
            run_cmd(
                [
                    sys.executable,
                    "move_to_detected_dent_center.py",
                    "--dl50-port",
                    args.dl50_port,
                    "--servo-port",
                    args.servo_port,
                    "--laser-id",
                    str(args.laser_id),
                    "--wheel-id",
                    str(args.wheel_id),
                    "--detected-region",
                    str(dent_csv),
                    "--wheel-forward-speed",
                    str(args.move_wheel_forward_speed),
                    "--wheel-reverse-speed",
                    str(args.move_wheel_reverse_speed),
                ]
            )
        else:
            print("[MOVE] 中心へ移動する場合は、--move-after-analysis を付けて再実行するか、move_to_detected_dent_center.py を使ってください。")

    finally:
        # Leave existing server alone. If this script started one, keep it alive
        # during normal use so the user can inspect the generated pages.
        if http_proc is not None:
            print(f"[HTTP] Server is still running for viewing: http://127.0.0.1:{args.http_port}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
