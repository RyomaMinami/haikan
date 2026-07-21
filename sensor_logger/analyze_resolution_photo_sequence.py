#!/usr/bin/env python3
"""
Analyze a sequence of outside photos for laser-center offset.

The photos are assumed to be taken in the same order as the resolution study
rows, e.g. 2701.jpg -> a5_x1, 2702.jpg -> a5_x2, ...

Method:
  - Fit an ellipse to the yellow dent opening/film area.
  - Detect the red/pink laser spot, then use the white laser core as the center.
  - Convert the laser-center offset to mm by normalizing against the fitted
    ellipse axes and the known dent diameter. Optionally, the short axis is
    treated as the pipe circumferential direction on a cylindrical outer
    surface instead of a flat plane.

This is a photo-based estimate. It is useful for relative comparison between
conditions, but it still contains camera-perspective and film-wrinkle error.
"""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import numpy as np


DEFAULT_RESULTS = Path("pipe154_resolution_study_full") / "pipe154_auto_scan_m115_115_resolution_results.csv"
DEFAULT_PHOTO_DIR = Path(r"C:\Users\minam\Downloads\Mobile Devices")


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


def read_results(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def find_photos(photo_dir: Path, prefix: str, count: int) -> list[Path]:
    photos = []
    for i in range(1, count + 1):
        path = photo_dir / f"{prefix}{i:02d}.jpg"
        if not path.exists():
            path = photo_dir / f"{prefix}{i}.jpg"
        if not path.exists():
            raise SystemExit(f"Missing photo for index {i}: {photo_dir}\\{prefix}{i:02d}.jpg")
        photos.append(path)
    return photos


def largest_contour(mask: np.ndarray):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    return max(contours, key=cv2.contourArea)


def fit_yellow_ellipse(image_bgr: np.ndarray):
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    # Yellow film: fairly broad threshold to absorb shadows and highlights.
    # The cardboard is also yellow-brown, so saturation must be high enough to
    # keep the mask on the yellow film instead of leaking into the pipe cover.
    mask = cv2.inRange(hsv, np.array([18, 80, 60]), np.array([45, 255, 255]))
    kernel = np.ones((15, 15), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((7, 7), np.uint8), iterations=1)

    contour = largest_contour(mask)
    if contour is None or len(contour) < 5:
        raise RuntimeError("Could not find yellow dent region.")
    area = cv2.contourArea(contour)
    if area < 10000:
        raise RuntimeError(f"Yellow region too small: area={area}")

    ellipse = cv2.fitEllipse(contour)
    (cx, cy), (axis1, axis2), angle_deg = ellipse
    # cv2 returns full diameters; keep major/minor explicit.
    if axis1 >= axis2:
        major = axis1
        minor = axis2
        major_angle_deg = angle_deg
    else:
        major = axis2
        minor = axis1
        major_angle_deg = angle_deg + 90.0
    return {
        "mask": mask,
        "contour": contour,
        "ellipse": ellipse,
        "center": np.array([cx, cy], dtype=float),
        "major_px": float(major),
        "minor_px": float(minor),
        "major_angle_deg": float(major_angle_deg),
        "area_px": float(area),
    }


def detect_laser_center(image_bgr: np.ndarray, opening_mask: np.ndarray):
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    b, g, r = cv2.split(image_bgr)

    # Keep this stricter than a generic red mask. The yellow film and cardboard
    # can look orange in photos, so use magenta/red excess plus a bright-core
    # fallback inside the yellow opening.
    red_excess = r.astype(np.int16) - g.astype(np.int16)
    magenta_rgb = (red_excess >= 60) & (r >= 150) & (b >= 60)
    magenta_hsv = (
        ((hsv[:, :, 0] <= 8) | (hsv[:, :, 0] >= 165))
        & (hsv[:, :, 1] >= 80)
        & (hsv[:, :, 2] >= 130)
    )
    base_laser_mask = magenta_rgb | magenta_hsv

    support_masks = [opening_mask > 0]
    # Coarse/failed center estimates can put the laser close to the edge of the
    # yellow opening. Include a modestly expanded opening before giving up.
    expanded = cv2.dilate(opening_mask, np.ones((45, 45), np.uint8), iterations=1)
    support_masks.append(expanded > 0)
    support_masks.append(np.ones(opening_mask.shape, dtype=bool))

    contour = None
    mask = None
    for support_mask in support_masks:
        candidate = (base_laser_mask & support_mask).astype(np.uint8) * 255
        candidate = cv2.morphologyEx(candidate, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8), iterations=1)
        candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=2)
        contour = largest_contour(candidate)
        if contour is not None:
            mask = candidate
            break

    if contour is None or mask is None:
        raise RuntimeError("Could not find laser spot.")
    spot_mask = np.zeros(mask.shape, dtype=np.uint8)
    cv2.drawContours(spot_mask, [contour], -1, 255, thickness=-1)

    # Use the brightest local core inside the laser halo. A fixed "white"
    # threshold is too brittle because the phone camera often records the core
    # as pale magenta rather than pure white.
    core_score = (
        gray.astype(np.float32)
        + 0.40 * b.astype(np.float32)
        + 0.20 * g.astype(np.float32)
    ) * (spot_mask > 0)
    spot_scores = core_score[spot_mask > 0]
    if len(spot_scores) >= 50:
        threshold = float(np.percentile(spot_scores, 99.5))
        core_mask = (core_score >= threshold) & (spot_mask > 0)
        if int(np.count_nonzero(core_mask)) < 8:
            threshold = float(np.percentile(spot_scores, 99.0))
            core_mask = (core_score >= threshold) & (spot_mask > 0)
        weights = core_score * core_mask
    else:
        red_weights = np.maximum(
            r.astype(np.float32) - 0.5 * g.astype(np.float32) - 0.25 * b.astype(np.float32),
            0,
        )
        weights = red_weights * (spot_mask > 0)
    ys, xs = np.nonzero(weights > 0)
    if len(xs) == 0:
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            raise RuntimeError("Laser contour has zero area.")
        return np.array([moments["m10"] / moments["m00"], moments["m01"] / moments["m00"]], dtype=float), mask, contour

    w = weights[ys, xs]
    cx = float(np.average(xs, weights=w))
    cy = float(np.average(ys, weights=w))
    return np.array([cx, cy], dtype=float), mask, contour


def ellipse_components_px(center: np.ndarray, laser: np.ndarray, major_angle_deg: float):
    dx, dy = laser - center
    theta = math.radians(major_angle_deg)
    unit_major = np.array([math.cos(theta), math.sin(theta)])
    unit_minor = np.array([-math.sin(theta), math.cos(theta)])
    comp_major_px = float(dx * unit_major[0] + dy * unit_major[1])
    comp_minor_px = float(dx * unit_minor[0] + dy * unit_minor[1])
    return comp_major_px, comp_minor_px


def ellipse_normalized_offset_mm(center: np.ndarray, laser: np.ndarray, major_px: float, minor_px: float, major_angle_deg: float, diameter_mm: float):
    comp_major_px, comp_minor_px = ellipse_components_px(center, laser, major_angle_deg)

    radius_mm = diameter_mm / 2.0
    comp_major_mm = comp_major_px / (major_px / 2.0) * radius_mm
    comp_minor_mm = comp_minor_px / (minor_px / 2.0) * radius_mm
    offset_mm = math.hypot(comp_major_mm, comp_minor_mm)
    return comp_major_mm, comp_minor_mm, offset_mm


def cylindrical_offset_mm(
    center: np.ndarray,
    laser: np.ndarray,
    major_px: float,
    minor_px: float,
    major_angle_deg: float,
    dent_diameter_mm: float,
    pipe_diameter_mm: float,
):
    """Estimate offset on the outside of a cylindrical pipe.

    The fitted ellipse's major axis is treated as the pipe-axis direction and
    the minor axis as the pipe circumferential direction. The circumferential
    component is unprojected with x = R * sin(s / R), where s is surface
    distance around the pipe.
    """
    comp_major_px, comp_minor_px = ellipse_components_px(center, laser, major_angle_deg)
    dent_radius_mm = dent_diameter_mm / 2.0
    pipe_radius_mm = pipe_diameter_mm / 2.0

    axial_mm = comp_major_px / (major_px / 2.0) * dent_radius_mm

    visible_half_width_mm = pipe_radius_mm * math.sin(min(dent_radius_mm / pipe_radius_mm, math.pi / 2.0))
    projected_mm = comp_minor_px / (minor_px / 2.0) * visible_half_width_mm
    projected_mm = max(-pipe_radius_mm, min(pipe_radius_mm, projected_mm))
    circumferential_mm = pipe_radius_mm * math.asin(projected_mm / pipe_radius_mm)

    offset_mm = math.hypot(axial_mm, circumferential_mm)
    return axial_mm, circumferential_mm, offset_mm


def draw_cross(img: np.ndarray, point: tuple[int, int], color: tuple[int, int, int], size: int = 14, thickness: int = 2) -> None:
    x, y = point
    cv2.line(img, (x - size, y), (x + size, y), color, thickness, cv2.LINE_AA)
    cv2.line(img, (x, y - size), (x, y + size), color, thickness, cv2.LINE_AA)
    cv2.circle(img, point, 3, color, -1, cv2.LINE_AA)


def annotate_image(
    image_bgr: np.ndarray,
    out_path: Path,
    condition: str,
    ellipse_info: dict,
    laser_center: np.ndarray,
    offset_mm: float,
) -> None:
    annotated = image_bgr.copy()
    cv2.ellipse(annotated, ellipse_info["ellipse"], (0, 255, 255), 4)
    center = tuple(np.round(ellipse_info["center"]).astype(int))
    laser = tuple(np.round(laser_center).astype(int))
    draw_cross(annotated, center, (255, 0, 0))
    draw_cross(annotated, laser, (0, 255, 0))
    cv2.line(annotated, center, laser, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(
        annotated,
        f"{condition}  offset={offset_mm:.2f} mm",
        (24, 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.25,
        (255, 255, 255),
        4,
        cv2.LINE_AA,
    )
    cv2.putText(
        annotated,
        "blue=dent center, green=laser",
        (24, 92),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.0,
        (255, 255, 255),
        3,
        cv2.LINE_AA,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), annotated)


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "condition",
        "photo",
        "angle_step_deg",
        "x_step_mm",
        "point_cloud_error_mm",
        "photo_offset_mm",
        "photo_relative_to_a5_x1_mm",
        "photo_major_mm",
        "photo_minor_mm",
        "photo_flat_offset_mm",
        "photo_flat_major_mm",
        "photo_flat_minor_mm",
        "pipe_diameter_mm",
        "dent_center_x_px",
        "dent_center_y_px",
        "laser_x_px",
        "laser_y_px",
        "ellipse_major_px",
        "ellipse_minor_px",
        "annotated_image",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_graph(path: Path, rows: list[dict[str, object]]) -> None:
    conditions = [str(row["condition"]) for row in rows]
    photo_offsets = [float(row["photo_offset_mm"]) for row in rows]
    photo_relative = [float(row["photo_relative_to_a5_x1_mm"]) for row in rows]
    pc_offsets = [float(row["point_cloud_error_mm"]) for row in rows]

    x = np.arange(len(rows))
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(13, 9), sharex=True)
    ax.plot(x, photo_offsets, marker="o", linewidth=2.5, label="photo absolute laser offset, cylinder corrected")
    ax.set_ylabel("photo offset [mm]")
    ax.set_title("Resolution condition vs laser-center offset")
    ax.grid(True, alpha=0.3)
    ax.legend()

    ax2.plot(x, photo_relative, marker="o", linewidth=2.5, label="photo change vs a5_x1")
    ax2.plot(x, pc_offsets, marker="s", linewidth=2.0, label="point-cloud estimate shift vs a5_x1")
    ax.set_xticks(x)
    ax2.set_xticks(x)
    ax2.set_xticklabels(conditions, rotation=35, ha="right")
    ax2.set_ylabel("relative error [mm]")
    ax2.grid(True, alpha=0.3)
    ax2.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def write_html(path: Path, rows: list[dict[str, object]], graph_path: Path) -> None:
    rel_graph = graph_path.name
    table_rows = []
    for row in rows:
        img = Path(str(row["annotated_image"])).name
        table_rows.append(
            "<tr>"
            f"<td>{row['condition']}</td>"
            f"<td>{row['angle_step_deg']}</td>"
            f"<td>{row['x_step_mm']}</td>"
            f"<td>{float(row['photo_offset_mm']):.2f}</td>"
            f"<td>{float(row['photo_relative_to_a5_x1_mm']):.2f}</td>"
            f"<td>{float(row['point_cloud_error_mm']):.2f}</td>"
            f"<td><a href=\"{img}\">{img}</a></td>"
            "</tr>"
        )
    html = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>Resolution Photo Offset Analysis</title>
  <style>
    body {{ font-family: Arial, "Meiryo", sans-serif; margin: 24px; color: #202124; }}
    h1 {{ font-size: 24px; }}
    img.graph {{ max-width: 100%; border: 1px solid #d0d7de; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; font-size: 13px; }}
    th, td {{ border: 1px solid #d0d7de; padding: 6px 8px; text-align: right; }}
    th:first-child, td:first-child, th:last-child, td:last-child {{ text-align: left; }}
    th {{ background: #f6f8fa; }}
    .note {{ background: #fff8c5; border: 1px solid #d4a72c; padding: 10px 12px; border-radius: 6px; }}
  </style>
</head>
<body>
  <h1>Resolution Photo Offset Analysis</h1>
  <p class="note">
    黄色領域を楕円フィットしてくぼみ中心、赤/ピンク領域からレーザー中心を推定し、
    154mm径換算で中心ずれを求めています。写真角度と黄色フィルムのしわによる誤差を含みます。
  </p>
  <img class="graph" src="{rel_graph}" alt="offset graph">
  <table>
    <thead>
      <tr>
        <th>condition</th>
        <th>angle step deg</th>
        <th>x step mm</th>
        <th>photo offset mm</th>
        <th>photo change vs a5_x1 mm</th>
        <th>point-cloud shift mm</th>
        <th>annotation</th>
      </tr>
    </thead>
    <tbody>
      {''.join(table_rows)}
    </tbody>
  </table>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze laser-center error from sequential resolution-condition photos.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--photo-dir", type=Path, default=DEFAULT_PHOTO_DIR)
    parser.add_argument("--photo-prefix", default="27")
    parser.add_argument("--dent-diameter-mm", type=float, default=154.0)
    parser.add_argument("--pipe-diameter-mm", type=float, default=250.0)
    parser.add_argument("--output-dir", type=Path, default=Path("pipe154_resolution_photo_analysis"))
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    if not args.results.is_absolute():
        args.results = script_dir / args.results
    if not args.output_dir.is_absolute():
        args.output_dir = script_dir / args.output_dir
    args.output_dir.mkdir(parents=True, exist_ok=True)

    result_rows = read_results(args.results)
    photos = find_photos(args.photo_dir, args.photo_prefix, len(result_rows))

    rows: list[dict[str, object]] = []
    for i, (result_row, photo) in enumerate(zip(result_rows, photos), start=1):
        condition = str(result_row.get("condition", f"photo{i:02d}"))
        image = cv2.imread(str(photo))
        if image is None:
            raise RuntimeError(f"Could not read image: {photo}")

        ellipse_info = fit_yellow_ellipse(image)
        opening_mask = np.zeros(image.shape[:2], dtype=np.uint8)
        cv2.drawContours(opening_mask, [ellipse_info["contour"]], -1, 255, thickness=-1)
        laser_center, laser_mask, laser_contour = detect_laser_center(image, opening_mask)
        flat_major_mm, flat_minor_mm, flat_offset_mm = ellipse_normalized_offset_mm(
            ellipse_info["center"],
            laser_center,
            ellipse_info["major_px"],
            ellipse_info["minor_px"],
            ellipse_info["major_angle_deg"],
            args.dent_diameter_mm,
        )
        major_mm, minor_mm, offset_mm = cylindrical_offset_mm(
            ellipse_info["center"],
            laser_center,
            ellipse_info["major_px"],
            ellipse_info["minor_px"],
            ellipse_info["major_angle_deg"],
            args.dent_diameter_mm,
            args.pipe_diameter_mm,
        )

        annotated = args.output_dir / f"{i:02d}_{condition}_{photo.stem}_annotated.jpg"
        annotate_image(image, annotated, condition, ellipse_info, laser_center, offset_mm)

        rows.append(
            {
                "condition": condition,
                "photo": str(photo),
                "angle_step_deg": result_row.get("angle_step_deg", ""),
                "x_step_mm": result_row.get("x_step_mm", ""),
                "point_cloud_error_mm": parse_float(result_row.get("error_surface_mm")) or 0.0,
                "photo_offset_mm": offset_mm,
                "photo_major_mm": major_mm,
                "photo_minor_mm": minor_mm,
                "photo_flat_offset_mm": flat_offset_mm,
                "photo_flat_major_mm": flat_major_mm,
                "photo_flat_minor_mm": flat_minor_mm,
                "pipe_diameter_mm": args.pipe_diameter_mm,
                "dent_center_x_px": float(ellipse_info["center"][0]),
                "dent_center_y_px": float(ellipse_info["center"][1]),
                "laser_x_px": float(laser_center[0]),
                "laser_y_px": float(laser_center[1]),
                "ellipse_major_px": ellipse_info["major_px"],
                "ellipse_minor_px": ellipse_info["minor_px"],
                "annotated_image": str(annotated),
            }
        )

        print(f"{condition}: photo_offset={offset_mm:.2f} mm  photo={photo.name}")

    if rows:
        reference_offset = float(rows[0]["photo_offset_mm"])
        for row in rows:
            row["photo_relative_to_a5_x1_mm"] = abs(float(row["photo_offset_mm"]) - reference_offset)

    csv_path = args.output_dir / "resolution_photo_offsets.csv"
    graph_path = args.output_dir / "resolution_photo_offsets.png"
    html_path = args.output_dir / "resolution_photo_offsets.html"
    write_csv(csv_path, rows)
    write_graph(graph_path, rows)
    write_html(html_path, rows, graph_path)

    print(f"Wrote CSV: {csv_path}")
    print(f"Wrote graph: {graph_path}")
    print(f"Wrote HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
