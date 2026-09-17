#!/usr/bin/env python3
"""Build a pipe inner-surface mesh from the axis-corrected LK-G85A scan."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import trimesh
from matplotlib import colors
from scipy.signal import savgol_filter


def load_tracks(path: Path) -> list[dict[str, object]]:
    groups: dict[float, list[tuple[float, float, float]]] = defaultdict(list)
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                commanded_angle = float(row["angle_deg"])
                x = float(row["x_zero_mm"])
                theta = float(row["fitted_theta_deg"])
                radius = float(row["fitted_radius_mm"])
            except (KeyError, TypeError, ValueError):
                continue
            if all(math.isfinite(v) for v in (commanded_angle, x, theta, radius)):
                groups[commanded_angle].append((x, theta, radius))

    tracks: list[dict[str, object]] = []
    for commanded_angle, values in groups.items():
        data = np.asarray(values, dtype=float)
        order = np.argsort(data[:, 0])
        data = data[order]
        x_rounded = np.round(data[:, 0], 3)
        unique_x, inverse = np.unique(x_rounded, return_inverse=True)
        radius_sum = np.bincount(inverse, weights=data[:, 2])
        radius_count = np.bincount(inverse)
        radius = radius_sum / np.maximum(radius_count, 1)
        tracks.append({
            "commanded_angle_deg": commanded_angle,
            "theta_deg": float(np.median(data[:, 1])),
            "x_mm": unique_x,
            "radius_mm": radius,
        })
    tracks.sort(key=lambda item: float(item["theta_deg"]))
    if len(tracks) < 3:
        raise ValueError(f"Only {len(tracks)} usable angular tracks were found in {path}")
    return tracks


def common_grid(tracks: list[dict[str, object]], axial_step_mm: float,
                smoothing_window: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x_min = max(float(np.min(t["x_mm"])) for t in tracks)
    x_max = min(float(np.max(t["x_mm"])) for t in tracks)
    x_grid = np.arange(x_min, x_max + 0.5 * axial_step_mm, axial_step_mm)
    theta = np.asarray([float(t["theta_deg"]) for t in tracks], dtype=float)
    radius_columns = []
    for track in tracks:
        column = np.interp(x_grid, track["x_mm"], track["radius_mm"])
        if smoothing_window >= 5 and len(column) >= smoothing_window:
            window = smoothing_window if smoothing_window % 2 == 1 else smoothing_window + 1
            window = min(window, len(column) if len(column) % 2 == 1 else len(column) - 1)
            if window >= 5:
                column = savgol_filter(column, window_length=window, polyorder=2, mode="interp")
        radius_columns.append(column)
    radius = np.column_stack(radius_columns)
    return x_grid, theta, radius


def make_full_grid(measured_theta: np.ndarray, measured_radius: np.ndarray,
                   angular_step_deg: float, nominal_radius_mm: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    full_theta = np.arange(-180.0, 180.0, angular_step_deg)
    full_radius = np.empty((measured_radius.shape[0], len(full_theta)), dtype=float)
    measured_mask = (full_theta >= measured_theta[0]) & (full_theta <= measured_theta[-1])
    for i, row in enumerate(measured_radius):
        full_radius[i] = nominal_radius_mm
        full_radius[i, measured_mask] = np.interp(
            full_theta[measured_mask], measured_theta, row
        )
    return full_theta, full_radius, measured_mask


def build_mesh(x: np.ndarray, theta_deg: np.ndarray, radius: np.ndarray,
               wrap_angle: bool) -> trimesh.Trimesh:
    theta = np.radians(theta_deg)
    vertices = np.empty((len(x) * len(theta), 3), dtype=float)
    for i, x_value in enumerate(x):
        start = i * len(theta)
        stop = start + len(theta)
        vertices[start:stop, 0] = x_value
        vertices[start:stop, 1] = radius[i] * np.cos(theta)
        vertices[start:stop, 2] = radius[i] * np.sin(theta)

    faces: list[tuple[int, int, int]] = []
    angular_cells = len(theta) if wrap_angle else len(theta) - 1
    for i in range(len(x) - 1):
        row0 = i * len(theta)
        row1 = (i + 1) * len(theta)
        for j in range(angular_cells):
            jn = (j + 1) % len(theta)
            a, b = row0 + j, row0 + jn
            c, d = row1 + j, row1 + jn
            faces.append((a, c, b))
            faces.append((b, c, d))
    mesh = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=False)
    mesh.remove_unreferenced_vertices()
    return mesh


def build_wall_mesh(x: np.ndarray, theta_deg: np.ndarray, inner_radius: np.ndarray,
                    thickness_mm: float, wrap_angle: bool) -> trimesh.Trimesh:
    """Create a closed pipe-wall solid using a radial constant-thickness offset."""
    theta = np.radians(theta_deg)
    n_x, n_t = len(x), len(theta)
    inner_vertices = np.empty((n_x * n_t, 3), dtype=float)
    outer_vertices = np.empty_like(inner_vertices)
    outer_radius = inner_radius + thickness_mm
    for i, x_value in enumerate(x):
        start, stop = i * n_t, (i + 1) * n_t
        inner_vertices[start:stop, 0] = x_value
        inner_vertices[start:stop, 1] = inner_radius[i] * np.cos(theta)
        inner_vertices[start:stop, 2] = inner_radius[i] * np.sin(theta)
        outer_vertices[start:stop, 0] = x_value
        outer_vertices[start:stop, 1] = outer_radius[i] * np.cos(theta)
        outer_vertices[start:stop, 2] = outer_radius[i] * np.sin(theta)

    vertices = np.vstack((inner_vertices, outer_vertices))
    outer_offset = len(inner_vertices)
    faces: list[tuple[int, int, int]] = []
    angular_cells = n_t if wrap_angle else n_t - 1

    # Inner surface normals face the cavity; outer surface normals face outward.
    for i in range(n_x - 1):
        row0, row1 = i * n_t, (i + 1) * n_t
        for j in range(angular_cells):
            jn = (j + 1) % n_t
            a, b, c, d = row0 + j, row0 + jn, row1 + j, row1 + jn
            faces.extend(((a, c, b), (b, c, d)))
            ao, bo, co, do = a + outer_offset, b + outer_offset, c + outer_offset, d + outer_offset
            faces.extend(((ao, bo, co), (bo, do, co)))

    # Close the two axial ends with annular faces.
    for j in range(angular_cells):
        jn = (j + 1) % n_t
        ia, ib = j, jn
        oa, ob = ia + outer_offset, ib + outer_offset
        faces.extend(((ia, ib, oa), (ib, ob, oa)))
        ia = (n_x - 1) * n_t + j
        ib = (n_x - 1) * n_t + jn
        oa, ob = ia + outer_offset, ib + outer_offset
        faces.extend(((ia, oa, ib), (ib, oa, ob)))

    # A measured-only angular sector also needs two longitudinal side walls.
    if not wrap_angle:
        for edge_j in (0, n_t - 1):
            for i in range(n_x - 1):
                ia = i * n_t + edge_j
                ib = (i + 1) * n_t + edge_j
                oa, ob = ia + outer_offset, ib + outer_offset
                if edge_j == 0:
                    faces.extend(((ia, oa, ib), (ib, oa, ob)))
                else:
                    faces.extend(((ia, ib, oa), (ib, ob, oa)))

    mesh = trimesh.Trimesh(vertices=vertices, faces=np.asarray(faces), process=True)
    mesh.remove_unreferenced_vertices()
    mesh.fix_normals()
    return mesh


def deviation_colors(radius: np.ndarray, nominal_radius_mm: float,
                     measured_mask: np.ndarray | None = None) -> np.ndarray:
    deviation = radius.reshape(-1) - nominal_radius_mm
    norm = colors.TwoSlopeNorm(vmin=-5.0, vcenter=0.0, vmax=7.0)
    rgba = (plt.colormaps["coolwarm"](norm(deviation)) * 255).astype(np.uint8)
    if measured_mask is not None:
        mask = np.tile(measured_mask, radius.shape[0])
        rgba[~mask] = np.array([180, 180, 180, 255], dtype=np.uint8)
    return rgba


def write_preview(path: Path, x: np.ndarray, measured_theta: np.ndarray,
                  measured_radius: np.ndarray, full_theta: np.ndarray,
                  full_radius: np.ndarray, nominal_radius_mm: float) -> None:
    fig = plt.figure(figsize=(15, 8))
    ax3d = fig.add_subplot(1, 2, 1, projection="3d")
    stride_x = max(1, len(x) // 180)
    xx, tt = np.meshgrid(x[::stride_x], np.radians(full_theta), indexing="ij")
    rr = full_radius[::stride_x]
    yy = rr * np.cos(tt)
    zz = rr * np.sin(tt)
    face_colors = plt.colormaps["coolwarm"](
        colors.TwoSlopeNorm(vmin=-5.0, vcenter=0.0, vmax=7.0)(rr - nominal_radius_mm)
    )
    ax3d.plot_surface(xx, yy, zz, facecolors=face_colors, linewidth=0, antialiased=False,
                      rstride=1, cstride=1, shade=False)
    ax3d.set_xlabel("Pipe axis X [mm]")
    ax3d.set_ylabel("Y [mm]")
    ax3d.set_zlabel("Z [mm]")
    ax3d.set_title("Reconstructed full inner surface")
    ax3d.set_box_aspect((max(x) - min(x), 240, 240))
    ax3d.view_init(elev=24, azim=-58)

    ax = fig.add_subplot(1, 2, 2)
    image = ax.pcolormesh(x, measured_theta, (measured_radius - nominal_radius_mm).T,
                          cmap="coolwarm",
                          norm=colors.TwoSlopeNorm(vmin=-5.0, vcenter=0.0, vmax=7.0),
                          shading="auto")
    ax.set_xlabel("Pipe axis X [mm]")
    ax.set_ylabel("Measured angle [deg]")
    ax.set_title("Measured radial deviation [mm]")
    fig.colorbar(image, ax=ax, label="Radius - 120 mm [mm]")
    fig.suptitle("LK-G85A measured pipe model")
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=here.parent / "pipe154_auto_scan_m115_115_point_cloud_axis_corrected.csv",
    )
    parser.add_argument("--output-dir", type=Path, default=here / "output")
    parser.add_argument("--axial-step-mm", type=float, default=2.0)
    parser.add_argument("--angular-step-deg", type=float, default=5.0)
    parser.add_argument("--nominal-radius-mm", type=float, default=120.0)
    parser.add_argument("--wall-thickness-mm", type=float, default=5.0)
    parser.add_argument("--smoothing-window", type=int, default=7)
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    tracks = load_tracks(input_path)
    x, measured_theta, measured_radius = common_grid(
        tracks, args.axial_step_mm, args.smoothing_window
    )
    full_theta, full_radius, measured_mask = make_full_grid(
        measured_theta, measured_radius, args.angular_step_deg, args.nominal_radius_mm
    )

    measured_mesh = build_mesh(x, measured_theta, measured_radius, wrap_angle=False)
    full_mesh = build_mesh(x, full_theta, full_radius, wrap_angle=True)
    measured_wall_mesh = build_wall_mesh(
        x, measured_theta, measured_radius, args.wall_thickness_mm, wrap_angle=False
    )
    full_wall_mesh = build_wall_mesh(
        x, full_theta, full_radius, args.wall_thickness_mm, wrap_angle=True
    )
    measured_mesh.visual.vertex_colors = deviation_colors(measured_radius, args.nominal_radius_mm)
    full_mesh.visual.vertex_colors = deviation_colors(full_radius, args.nominal_radius_mm, measured_mask)

    measured_stl = output_dir / "pipe_inner_measured_only.stl"
    measured_ply = output_dir / "pipe_inner_measured_only_colored.ply"
    full_stl = output_dir / "pipe_inner_full360_nominal_fill.stl"
    full_ply = output_dir / "pipe_inner_full360_nominal_fill_colored.ply"
    measured_wall_stl = output_dir / "pipe_wall_5mm_measured_sector.stl"
    full_wall_stl = output_dir / "pipe_wall_5mm_full360_nominal_fill.stl"
    preview = output_dir / "pipe_model_preview.png"
    measured_mesh.export(measured_stl)
    measured_mesh.export(measured_ply)
    full_mesh.export(full_stl)
    full_mesh.export(full_ply)
    measured_wall_mesh.export(measured_wall_stl)
    full_wall_mesh.export(full_wall_stl)
    write_preview(
        preview, x, measured_theta, measured_radius, full_theta, full_radius,
        args.nominal_radius_mm,
    )

    metadata = {
        "source_csv": str(input_path),
        "coordinate_system": "X=pipe axis, Y/Z=axis-corrected cross section, units=mm",
        "axial_range_mm": [float(x[0]), float(x[-1])],
        "axial_step_mm": args.axial_step_mm,
        "measured_angle_range_deg": [float(measured_theta[0]), float(measured_theta[-1])],
        "measured_angle_tracks": int(len(measured_theta)),
        "full_model_angular_step_deg": args.angular_step_deg,
        "nominal_radius_mm": args.nominal_radius_mm,
        "wall_thickness_mm": args.wall_thickness_mm,
        "full_wall_mesh_watertight": bool(full_wall_mesh.is_watertight),
        "measured_sector_wall_mesh_watertight": bool(measured_wall_mesh.is_watertight),
        "smoothing_window": args.smoothing_window,
        "measured_radius_range_mm": [float(np.min(measured_radius)), float(np.max(measured_radius))],
        "warning": "The unmeasured angular sector in the full360 model is filled with the nominal 120 mm radius.",
        "outputs": {
            "measured_stl": str(measured_stl),
            "measured_colored_ply": str(measured_ply),
            "full360_stl": str(full_stl),
            "full360_colored_ply": str(full_ply),
            "measured_sector_wall_stl": str(measured_wall_stl),
            "full360_wall_stl": str(full_wall_stl),
            "preview_png": str(preview),
        },
    }
    metadata_path = output_dir / "pipe_model_metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
