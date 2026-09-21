#!/usr/bin/env python3
"""Enumerate passive-wheel-on-ridge cases and first drive-wheel contact."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import trimesh
from scipy.optimize import brentq, minimize

from run_measured_pipe_simulator import (
    ACTIVE_DRIVE_WHEEL_INDICES,
    BIT_MODULE_POSITION_MM,
    DRIVE_WHEELS_MODULE_CAD_MM,
    DRILLING1_MODULE_POSITION_MM,
    DRILLING2_MODULE_POSITION_MM,
    DRILLING_WHEELS_MODULE_CAD_MM,
    NORMAL_PASSIVE_SEAT_Y_M,
    PASSIVE_WHEELS_CAD_MM,
    ROBOT_MODULE_POSITION_MM,
    SLIDER6_MAX_MM,
    SLIDER6_MIN_MM,
    SLIDER11_MIN_MM,
    cad_to_sim,
    drive_wheel_components,
    drilling_wheel_components,
    load_robot_mesh,
    passive_wheel_components,
)


# Use a tight tolerance for geometric contact.  The previous 0.25 mm display
# tolerance incorrectly marked nearby drive wheels as touching even though a
# positive gap remained at the rigid first-contact cylinder position.
CONTACT_TOLERANCE_MM = 0.02
RIDGE_MIN_HEIGHT_MM = 0.70
ROTATION9_MIN_DEG = -180.0
ROTATION9_MAX_DEG = 180.0
ROTATION9_PIVOT_CAD_MM = np.array([-59.0, -20.0, -300.0])
ROBOT_MASS_KG = 21.0
GRAVITY_M_S2 = 9.80665
WHEEL_RADIAL_STIFFNESS_N_PER_MM = 200.0
BODY_CONTACT_STIFFNESS_N_PER_MM = 5000.0


def load_geometry(
    asset_dir: Path,
) -> tuple[
    list[np.ndarray], list[np.ndarray], list[np.ndarray], dict[str, np.ndarray]
]:
    base = load_robot_mesh(asset_dir / "01_move_module.stl")
    motor = load_robot_mesh(asset_dir / "02_motor_module.stl")
    drilling1 = load_robot_mesh(asset_dir / "03_drilling_module1.stl")
    drilling2 = load_robot_mesh(asset_dir / "03_drilling_module2.stl")
    bit = load_robot_mesh(asset_dir / "04_bit.stl")
    motor_offset = ROBOT_MODULE_POSITION_MM + np.array([0.0, SLIDER6_MIN_MM, 0.0])
    minimum = np.minimum(base.bounds[0], motor.bounds[0] + motor_offset)
    maximum = np.maximum(base.bounds[1], motor.bounds[1] + motor_offset)
    cad_center = (minimum + maximum) * 0.5
    passive = [
        cad_to_sim(wheel.convex_hull.vertices - cad_center)
        for wheel in passive_wheel_components(base)
    ]
    drive = [
        cad_to_sim(wheel.convex_hull.vertices + motor_offset - cad_center)
        for wheel in drive_wheel_components(motor)
    ]
    drilling_wheel_meshes = drilling_wheel_components(drilling1)
    drilling = [
        cad_to_sim(
            wheel.convex_hull.vertices
            + DRILLING1_MODULE_POSITION_MM - cad_center
        )
        for wheel in drilling_wheel_meshes
    ]
    drilling1_parts = list(drilling1.split(only_watertight=False))
    drilling_wheel_centers = [
        np.asarray(wheel.bounding_box.centroid) for wheel in drilling_wheel_meshes
    ]
    drilling1_body_parts = [
        part for part in drilling1_parts
        if all(
            np.linalg.norm(
                np.asarray(part.bounding_box.centroid) - wheel_center
            ) > 0.01
            for wheel_center in drilling_wheel_centers
        )
    ]
    drilling1_body = trimesh.util.concatenate(drilling1_body_parts)
    bit_rotation = np.array([
        [1.0, 0.0, 0.0],
        [0.0, 0.0, -1.0],
        [0.0, 1.0, 0.0],
    ])
    drilling_modules = {
        "drilling_module1": cad_to_sim(
            np.asarray(drilling1_body.vertices)
            + DRILLING1_MODULE_POSITION_MM - cad_center
        ),
        "drilling_module2": cad_to_sim(
            np.asarray(drilling2.vertices)
            + DRILLING2_MODULE_POSITION_MM
            + np.array([0.0, SLIDER11_MIN_MM, 0.0]) - cad_center
        ),
        "bit": cad_to_sim(
            np.asarray(bit.vertices) @ bit_rotation.T
            + BIT_MODULE_POSITION_MM
            + np.array([0.0, SLIDER11_MIN_MM, 0.0]) - cad_center
        ),
    }
    return passive, drive, drilling, drilling_modules


def load_fixed_body_geometry(asset_dir: Path) -> dict[str, np.ndarray]:
    """Load non-wheel platform/motor surfaces for pipe interference checks."""
    base = load_robot_mesh(asset_dir / "01_move_module.stl")
    motor = load_robot_mesh(asset_dir / "02_motor_module.stl")
    motor_offset = ROBOT_MODULE_POSITION_MM + np.array(
        [0.0, SLIDER6_MIN_MM, 0.0]
    )
    cad_center = (
        np.minimum(base.bounds[0], motor.bounds[0] + motor_offset)
        + np.maximum(base.bounds[1], motor.bounds[1] + motor_offset)
    ) * 0.5

    base_parts = list(base.split(only_watertight=False))
    passive_parts = {
        id(min(
            base_parts,
            key=lambda part: np.linalg.norm(
                np.asarray(part.bounding_box.centroid) - center
            ),
        ))
        for center in PASSIVE_WHEELS_CAD_MM
    }
    base_body = trimesh.util.concatenate([
        part for part in base_parts if id(part) not in passive_parts
    ])

    motor_body_parts = []
    for part in motor.split(only_watertight=False):
        center = np.asarray(part.bounding_box.centroid)
        extent = np.asarray(part.bounding_box.extents)
        outer_tire = any(
            abs(center[0] - wheel_center[0]) < 20.0
            and abs(center[1] - wheel_center[1]) < 0.20
            and abs(center[2] - wheel_center[2]) < 0.20
            and extent[0] <= 15.0
            and extent[1] >= 39.5
            and extent[2] >= 39.5
            for wheel_center in DRIVE_WHEELS_MODULE_CAD_MM
        )
        if not outer_tire:
            motor_body_parts.append(part)
    motor_body = trimesh.util.concatenate(motor_body_parts)
    return {
        "move_module_body": cad_to_sim(
            np.asarray(base_body.vertices) - cad_center
        ),
        "motor_module_body_retracted": cad_to_sim(
            np.asarray(motor_body.vertices) + motor_offset - cad_center
        ),
    }


def pipe_radius_m(points: np.ndarray, estimation: dict) -> np.ndarray:
    x_mm = points[:, 0] * 1000.0
    theta_deg = np.degrees(np.arctan2(points[:, 2], points[:, 1]))
    lead = float(estimation["lead_mm_per_revolution"])
    profile = estimation["profile"]
    start = float(profile["crest_phase_at_theta0_mm"])
    distance = np.abs(
        np.mod(x_mm - start - lead * theta_deg / 360.0 + 0.5 * lead, lead)
        - 0.5 * lead
    )
    distance_knots = np.array([
        0.0,
        0.5 * float(profile["crest_width_90pct_height_mm"]),
        0.5 * float(profile["crest_width_80pct_height_mm"]),
        0.5 * float(profile["half_height_width_mm"]),
        0.5 * lead,
    ])
    height = float(profile["peak_to_valley_mm"])
    height_knots = height * np.array([1.0, 0.9, 0.8, 0.5, 0.0])
    depth_mm = np.interp(distance, distance_knots, height_knots)
    return (120.0 - depth_mm) * 0.001


def roll_about_body_axis(vertices: np.ndarray, roll_deg: float) -> np.ndarray:
    angle = np.radians(roll_deg)
    cosine, sine = np.cos(angle), np.sin(angle)
    points = vertices.copy()
    points[:, 1] = cosine * vertices[:, 1] - sine * vertices[:, 2]
    points[:, 2] = sine * vertices[:, 1] + cosine * vertices[:, 2]
    return points


def placed(vertices: np.ndarray, body_x: float, body_y: float,
           body_z: float, slider_mm: float = SLIDER6_MIN_MM,
           body_roll_deg: float = 0.0) -> np.ndarray:
    points = vertices.copy()
    points[:, 2] += (slider_mm - SLIDER6_MIN_MM) * 0.001
    points = roll_about_body_axis(points, body_roll_deg)
    points += np.array([body_x, body_y, body_z])
    return points


def clearance_mm(vertices: np.ndarray, pose: tuple[float, float, float],
                 estimation: dict, slider_mm: float = SLIDER6_MIN_MM,
                 body_roll_deg: float = 0.0) -> float:
    points = placed(vertices, *pose, slider_mm, body_roll_deg)
    radial = np.linalg.norm(points[:, 1:3], axis=1)
    return float(np.min(pipe_radius_m(points, estimation) - radial) * 1000.0)


def clearance_details(
    vertices: np.ndarray,
    pose: tuple[float, float, float],
    estimation: dict,
    body_roll_deg: float = 0.0,
    slider_mm: float = SLIDER6_MIN_MM,
) -> tuple[float, np.ndarray]:
    points = placed(vertices, *pose, slider_mm, body_roll_deg)
    radial = np.linalg.norm(points[:, 1:3], axis=1)
    gaps = pipe_radius_m(points, estimation) - radial
    index = int(np.argmin(gaps))
    return float(gaps[index] * 1000.0), points[index]


def rotate_about_pipe_axis(
    vertices: np.ndarray, pivot: np.ndarray, angle_deg: float
) -> np.ndarray:
    """Rotate local simulator vertices about rotation9 (the pipe/X axis)."""
    angle = np.radians(angle_deg)
    cosine, sine = np.cos(angle), np.sin(angle)
    relative = vertices - pivot
    rotated = relative.copy()
    rotated[:, 1] = cosine * relative[:, 1] - sine * relative[:, 2]
    rotated[:, 2] = sine * relative[:, 1] + cosine * relative[:, 2]
    return rotated + pivot


def local_ridge_height_mm(point: np.ndarray, estimation: dict) -> float:
    return float((0.120 - pipe_radius_m(point[None, :], estimation)[0]) * 1000.0)


def estimate_assembly_com(
    asset_dir: Path, cad_center: np.ndarray, rotation9_pivot: np.ndarray,
    rotation9_deg: float,
) -> tuple[np.ndarray, dict]:
    """Estimate COM from STL enclosed-volume ratios (provisional)."""
    base = load_robot_mesh(asset_dir / "01_move_module.stl")
    motor = load_robot_mesh(asset_dir / "02_motor_module.stl")
    drilling1 = load_robot_mesh(asset_dir / "03_drilling_module1.stl")
    drilling2 = load_robot_mesh(asset_dir / "03_drilling_module2.stl")
    bit = load_robot_mesh(asset_dir / "04_bit.stl")
    bit_rotation = np.array([
        [1.0, 0.0, 0.0],
        [0.0, 0.0, -1.0],
        [0.0, 1.0, 0.0],
    ])
    entries = [
        ("move_module", base, np.zeros(3), np.eye(3), False),
        ("motor_module", motor, ROBOT_MODULE_POSITION_MM + np.array(
            [0.0, SLIDER6_MIN_MM, 0.0]
        ), np.eye(3), False),
        ("drilling_module1", drilling1, DRILLING1_MODULE_POSITION_MM, np.eye(3), True),
        ("drilling_module2", drilling2, DRILLING2_MODULE_POSITION_MM + np.array(
            [0.0, SLIDER11_MIN_MM, 0.0]
        ), np.eye(3), True),
        ("bit", bit, BIT_MODULE_POSITION_MM + np.array(
            [0.0, SLIDER11_MIN_MM, 0.0]
        ), bit_rotation, True),
    ]
    weighted = np.zeros(3)
    total = 0.0
    parts = {}
    for name, mesh, offset, rotation, follows_rotation9 in entries:
        weight = max(abs(float(mesh.volume)), 1.0)
        centroid_cad = np.asarray(mesh.center_mass) @ rotation.T + offset
        centroid = cad_to_sim(centroid_cad - cad_center)
        if follows_rotation9:
            centroid = rotate_about_pipe_axis(
                centroid[None, :], rotation9_pivot, rotation9_deg
            )[0]
        weighted += weight * centroid
        total += weight
        parts[name] = {
            "volume_weight_fraction": weight,
            "center_sim_m": centroid.tolist(),
            "stl_watertight": bool(mesh.is_watertight),
        }
    com = weighted / total
    for item in parts.values():
        item["volume_weight_fraction"] /= total
    return com, parts


def settle_under_gravity(
    passive: list[np.ndarray],
    drilling_wheels: list[np.ndarray],
    drilling_modules: dict[str, np.ndarray],
    initial_pose: tuple[float, float, float],
    initial_roll_deg: float,
    com_local: np.ndarray,
    estimation: dict,
    fast: bool = False,
    fixed_body_roll_deg: float | None = None,
) -> tuple[tuple[float, float, float], float, dict]:
    """Find quasi-static Y/Z/roll equilibrium under gravity and compliance."""
    body_x, initial_y, initial_z = initial_pose
    wheel_sets = list(passive) + list(drilling_wheels)
    # Use a deterministic surface sample during optimization.  Exact full-mesh
    # clearances are recomputed after settlement for the reported result.
    collision_samples = []
    for vertices in drilling_modules.values():
        if len(vertices) <= 1500:
            collision_samples.append(vertices)
        else:
            radial = np.linalg.norm(vertices[:, 1:3], axis=1)
            exposed = np.flatnonzero(radial >= 0.104)
            if fast and len(exposed) > 800:
                exposed = exposed[np.linspace(0, len(exposed) - 1, 800, dtype=int)]
            background_count = 200 if fast else 500
            background = np.linspace(
                0, len(vertices) - 1, background_count, dtype=int
            )
            collision_samples.append(vertices[np.unique(np.r_[exposed, background])])

    def potential(q: np.ndarray) -> float:
        body_y, body_z = float(q[0]), float(q[1])
        roll_deg = (
            float(q[2]) if fixed_body_roll_deg is None
            else float(fixed_body_roll_deg)
        )
        pose = (body_x, body_y, body_z)
        wheel_penetrations = [
            max(0.0, -clearance_mm(
                wheel, pose, estimation, body_roll_deg=roll_deg
            ))
            for wheel in wheel_sets
        ]
        body_penetrations = [
            max(0.0, -clearance_mm(
                vertices, pose, estimation, body_roll_deg=roll_deg
            ))
            for vertices in collision_samples
        ]
        rolled_com = roll_about_body_axis(com_local[None, :], roll_deg)[0]
        com_height_mm = (body_z + rolled_com[2]) / 0.001
        gravity = ROBOT_MASS_KG * GRAVITY_M_S2 * com_height_mm
        wheel_energy = 0.5 * WHEEL_RADIAL_STIFFNESS_N_PER_MM * sum(
            value * value for value in wheel_penetrations
        )
        body_energy = 0.5 * BODY_CONTACT_STIFFNESS_N_PER_MM * sum(
            value * value for value in body_penetrations
        )
        return gravity + wheel_energy + body_energy

    bounds = [
        (initial_y - 0.015, initial_y + 0.015),
        (initial_z - 0.020, initial_z + 0.010),
        (initial_roll_deg - 50.0, initial_roll_deg + 50.0),
    ]
    if fixed_body_roll_deg is not None:
        bounds = bounds[:2]
        initial_q = np.array([initial_y, initial_z])
    else:
        initial_q = np.array([initial_y, initial_z, initial_roll_deg])
    result = minimize(
        potential,
        initial_q,
        method="Powell",
        bounds=bounds,
        options={
            "maxiter": 100 if fast else 240,
            "xtol": 2e-6 if fast else 1e-7,
            "ftol": 2e-5 if fast else 1e-7,
        },
    )
    body_y, body_z = float(result.x[0]), float(result.x[1])
    roll_deg = (
        float(result.x[2]) if fixed_body_roll_deg is None
        else float(fixed_body_roll_deg)
    )
    pose = (body_x, body_y, body_z)
    wheel_details = [
        clearance_details(wheel, pose, estimation, roll_deg)
        for wheel in wheel_sets
    ]
    wheel_gaps = [detail[0] for detail in wheel_details]
    wheel_normals = [
        WHEEL_RADIAL_STIFFNESS_N_PER_MM * max(0.0, -gap)
        for gap in wheel_gaps
    ]
    body_details = {
        name: clearance_details(
            vertices, pose, estimation, roll_deg
        )
        for name, vertices in drilling_modules.items()
    }
    body_gaps = {name: detail[0] for name, detail in body_details.items()}
    body_normals = {
        name: BODY_CONTACT_STIFFNESS_N_PER_MM * max(0.0, -gap)
        for name, gap in body_gaps.items()
    }
    vertical_support = 0.0
    lateral_support = 0.0
    contact_torque_x_n_m = 0.0
    rolled_com = roll_about_body_axis(com_local[None, :], roll_deg)[0]
    world_com = rolled_com + np.asarray(pose)
    for force, (_, point) in zip(wheel_normals, wheel_details):
        radial = float(np.linalg.norm(point[1:3]))
        if radial > 0.0:
            contact_force = np.array([
                0.0, force * (-point[1] / radial), force * (-point[2] / radial)
            ])
            lateral_support += contact_force[1]
            vertical_support += contact_force[2]
            contact_torque_x_n_m += float(
                np.cross(point - world_com, contact_force)[0]
            )
    for name, force in body_normals.items():
        point = body_details[name][1]
        radial = float(np.linalg.norm(point[1:3]))
        if radial > 0.0:
            contact_force = np.array([
                0.0, force * (-point[1] / radial), force * (-point[2] / radial)
            ])
            lateral_support += contact_force[1]
            vertical_support += contact_force[2]
            contact_torque_x_n_m += float(
                np.cross(point - world_com, contact_force)[0]
            )
    return pose, roll_deg, {
        "success": bool(result.success),
        "message": str(result.message),
        "potential_n_mm": float(result.fun),
        "wheel_clearance_mm": wheel_gaps,
        "wheel_normal_force_n": wheel_normals,
        "body_clearance_mm": body_gaps,
        "body_normal_force_n": body_normals,
        "total_support_force_n": float(sum(wheel_normals) + sum(body_normals.values())),
        "vertical_support_force_n": float(vertical_support),
        "lateral_force_residual_n": float(lateral_support),
        "roll_constraint_torque_n_m": (
            float(-contact_torque_x_n_m)
            if fixed_body_roll_deg is not None else 0.0
        ),
        "weight_n": ROBOT_MASS_KG * GRAVITY_M_S2,
        "contact_wheels": [
            index + 1 for index, force in enumerate(wheel_normals) if force >= 2.0
        ],
    }


def rotation9_contact_candidates(
    wheel: np.ndarray,
    drilling_modules: dict[str, np.ndarray],
    pivot: np.ndarray,
    pose: tuple[float, float, float],
    estimation: dict,
) -> list[dict]:
    """Find drilling-wheel contacts during one complete rotation9 sweep."""
    angles = np.linspace(ROTATION9_MIN_DEG, ROTATION9_MAX_DEG, 721)

    def wheel_gap(angle_deg: float) -> float:
        return clearance_mm(
            rotate_about_pipe_axis(wheel, pivot, angle_deg), pose, estimation
        )

    gaps = [wheel_gap(angle) for angle in angles]
    roots: list[float] = []
    for index in range(len(angles) - 1):
        if gaps[index] == 0.0:
            roots.append(float(angles[index]))
        elif gaps[index] * gaps[index + 1] < 0.0:
            roots.append(float(brentq(
                wheel_gap, float(angles[index]), float(angles[index + 1])
            )))

    candidates = []
    for angle in roots:
        rotated_wheel = rotate_about_pipe_axis(wheel, pivot, angle)
        wheel_clearance, contact_point = clearance_details(
            rotated_wheel, pose, estimation
        )
        module_details = {
            name: clearance_details(
                rotate_about_pipe_axis(vertices, pivot, angle), pose, estimation
            )
            for name, vertices in drilling_modules.items()
        }
        closest_module, (module_gap, module_point) = min(
            module_details.items(), key=lambda item: item[1][0]
        )
        ridge_height = local_ridge_height_mm(contact_point, estimation)
        on_ridge = ridge_height >= RIDGE_MIN_HEIGHT_MM
        # A small zero/tolerance at drilling_module1 is the intended wheel
        # contact.  A negative value means another surface has entered the pipe.
        collision_free = module_gap >= -CONTACT_TOLERANCE_MM
        candidates.append({
            "rotation9_deg": angle,
            "wheel_clearance_mm": wheel_clearance,
            "ridge_height_mm": ridge_height,
            "on_ridge": on_ridge,
            "collision_free": collision_free,
            "accepted": on_ridge and collision_free,
            "minimum_module_clearance_mm": module_gap,
            "closest_component": closest_module,
            "wheel_contact_point_world_m": contact_point.tolist(),
            "closest_module_point_world_m": module_point.tolist(),
            "component_clearance_mm": {
                name: detail[0] for name, detail in module_details.items()
            },
        })
    return candidates


def solve_zero(function, lower: float, upper: float) -> float:
    samples = np.linspace(lower, upper, 161)
    values = [function(value) for value in samples]
    roots = [
        brentq(function, samples[index], samples[index + 1])
        for index in range(len(samples) - 1)
        if values[index] == 0 or values[index] * values[index + 1] < 0
    ]
    if not roots:
        raise RuntimeError("No contact root was found in the permitted range")
    return min(roots, key=abs)


def align_selected_wheel(selected: np.ndarray, estimation: dict,
                         pipe_center_x: float) -> tuple[float, float]:
    """Align the selected passive wheel's lowest point with a helix crest."""
    body_y = NORMAL_PASSIVE_SEAT_Y_M
    body_z = 0.0
    lead_m = float(estimation["lead_mm_per_revolution"]) * 0.001
    start_m = float(estimation["profile"]["crest_phase_at_theta0_mm"]) * 0.001
    body_x = pipe_center_x
    for _ in range(4):
        provisional = placed(selected, body_x, body_y, body_z)
        radial = np.linalg.norm(provisional[:, 1:3], axis=1)
        index = int(np.argmax(radial))
        theta_turn = np.arctan2(provisional[index, 2], provisional[index, 1]) / (2 * np.pi)
        local_x = selected[index, 0]
        turn = round((pipe_center_x + local_x - start_m - lead_m * theta_turn) / lead_m)
        body_x = start_m + turn * lead_m + lead_m * theta_turn - local_x
        body_z = solve_zero(
            lambda z: clearance_mm(
                selected, (body_x, body_y, z), estimation
            ),
            -0.040, 0.040,
        )
    return body_x, body_z


def align_wheel_group(selected: list[np.ndarray], estimation: dict,
                      pipe_center_x: float) -> tuple[float, float]:
    """Place a group at the mean of their individual helical crest phases."""
    individual = [
        align_selected_wheel(wheel, estimation, pipe_center_x)
        for wheel in selected
    ]
    body_x = float(np.mean([pose[0] for pose in individual]))
    body_z_guess = float(np.mean([pose[1] for pose in individual]))
    body_z = solve_zero(
        lambda z: min(
            clearance_mm(
                wheel, (body_x, NORMAL_PASSIVE_SEAT_Y_M, z), estimation
            )
            for wheel in selected
        ),
        body_z_guess - 0.010,
        body_z_guess + 0.010,
    )
    return body_x, body_z


def drive_contact_stroke(vertices: np.ndarray,
                         pose: tuple[float, float, float],
                         estimation: dict,
                         body_roll_deg: float = 0.0) -> float | None:
    minimum = clearance_mm(
        vertices, pose, estimation, SLIDER6_MIN_MM, body_roll_deg
    )
    maximum = clearance_mm(
        vertices, pose, estimation, SLIDER6_MAX_MM, body_roll_deg
    )
    if minimum <= 0.0:
        return 0.0
    if maximum > 0.0:
        return None
    return brentq(
        lambda stroke: clearance_mm(
            vertices, pose, estimation, SLIDER6_MIN_MM + stroke, body_roll_deg
        ),
        0.0,
        SLIDER6_MAX_MM - SLIDER6_MIN_MM,
    )


def drive_first_contact(
    drive: list[np.ndarray], pose: tuple[float, float, float],
    estimation: dict, body_roll_deg: float = 0.0,
) -> dict:
    """First cylinder contact among the two active, drill-opposite wheels."""
    strokes = [None] * len(drive)
    for index in ACTIVE_DRIVE_WHEEL_INDICES:
        strokes[index] = drive_contact_stroke(
            drive[index], pose, estimation, body_roll_deg
        )
    reachable = [value for value in strokes if value is not None]
    stop_stroke = min(reachable) if reachable else None
    slider = (
        SLIDER6_MAX_MM if stop_stroke is None
        else SLIDER6_MIN_MM + stop_stroke
    )
    gaps = [None] * len(drive)
    for index in ACTIVE_DRIVE_WHEEL_INDICES:
        gaps[index] = clearance_mm(
            drive[index], pose, estimation, slider, body_roll_deg
        )
    return {
        "drive_contact_enabled": [
            index in ACTIVE_DRIVE_WHEEL_INDICES for index in range(len(drive))
        ],
        "drive_required_stroke_mm": strokes,
        "cylinder_first_contact_stroke_mm": stop_stroke,
        "drive_clearance_at_first_contact_mm": gaps,
        "drive_contact": [
            gap is not None and gap <= CONTACT_TOLERANCE_MM for gap in gaps
        ],
        "inactive_drive_position_note": (
            "Module-side wheels 3/4 are lower from mechanical play; "
            "offset not measured, hence excluded from contact."
        ),
    }


def main() -> None:
    here = Path(__file__).resolve().parent
    estimation_path = here / "output" / "helical_ridge_estimation.json"
    estimation = json.loads(estimation_path.read_text(encoding="utf-8"))
    assets = here.parent.parent / "raspi_tools" / "camera_stream" / "assets"
    passive, drive, drilling, drilling_modules = load_geometry(assets)
    base = load_robot_mesh(assets / "01_move_module.stl")
    motor = load_robot_mesh(assets / "02_motor_module.stl")
    motor_offset = ROBOT_MODULE_POSITION_MM + np.array([0.0, SLIDER6_MIN_MM, 0.0])
    cad_center = (
        np.minimum(base.bounds[0], motor.bounds[0] + motor_offset)
        + np.maximum(base.bounds[1], motor.bounds[1] + motor_offset)
    ) * 0.5
    rotation9_pivot = cad_to_sim(ROTATION9_PIVOT_CAD_MM - cad_center)
    pipe_metadata_path = here / "output" / "pipe_full360_helix_estimated.json"
    if pipe_metadata_path.exists():
        pipe_metadata = json.loads(pipe_metadata_path.read_text(encoding="utf-8"))
        x_min, x_max = (
            value * 0.001 for value in pipe_metadata["axial_range_mm"]
        )
    else:
        x_min, x_max = (
            value * 0.001 for value in estimation["measured_range"]["x_mm"]
        )
    pipe_center_x = 0.5 * (x_min + x_max)
    # The inferred right-hand helix puts wheels 2 and 3 on the same crest:
    # their individual crest phases differ by only about 2 mm, versus a
    # measured 25.9 mm-wide 90%-height crest.  They are therefore one physical
    # case rather than two duplicate cases.
    wheel_groups = [[0], [1, 2], [3]]
    cases = []
    rotation9_sweep_candidates = []
    for case_index, selected_indices in enumerate(wheel_groups, start=1):
        selected = [passive[index] for index in selected_indices]
        body_x, body_z = align_wheel_group(selected, estimation, pipe_center_x)
        pose = (body_x, NORMAL_PASSIVE_SEAT_Y_M, body_z)
        passive_clearances = [
            clearance_mm(wheel, pose, estimation) for wheel in passive
        ]
        drilling_clearances = [
            clearance_mm(wheel, pose, estimation) for wheel in drilling
        ]
        module_details = {
            name: clearance_details(vertices, pose, estimation)
            for name, vertices in drilling_modules.items()
        }
        closest_module, (module_gap, module_point) = min(
            module_details.items(), key=lambda item: item[1][0]
        )
        drive_state = drive_first_contact(drive, pose, estimation)
        cases.append({
            "case": case_index,
            "base_passive_case": case_index,
            "rotation9_deg": 0.0,
            "passive_wheels_on_ridge": [index + 1 for index in selected_indices],
            "passive_wheel_cad_centers_mm": [
                PASSIVE_WHEELS_CAD_MM[index].tolist()
                for index in selected_indices
            ],
            "body_pose_m": {"x": body_x, "y": NORMAL_PASSIVE_SEAT_Y_M, "z": body_z},
            "passive_clearance_mm": passive_clearances,
            "passive_contact": [
                gap <= CONTACT_TOLERANCE_MM for gap in passive_clearances
            ],
            "drilling_wheel_clearance_mm": drilling_clearances,
            "drilling_contact": [
                gap <= CONTACT_TOLERANCE_MM for gap in drilling_clearances
            ],
            "drilling_module_after_press": {
                "sequence": "checked after drive-wheel first contact",
                "contact": module_gap <= CONTACT_TOLERANCE_MM,
                "minimum_clearance_mm": module_gap,
                "closest_component": closest_module,
                "closest_point_world_m": module_point.tolist(),
                "component_clearance_mm": {
                    name: detail[0] for name, detail in module_details.items()
                },
            },
            **drive_state,
        })
        sweep = rotation9_contact_candidates(
            drilling[0], drilling_modules, rotation9_pivot, pose, estimation
        )
        for candidate in sweep:
            candidate["base_passive_case"] = case_index
        rotation9_sweep_candidates.extend(sweep)

    # Preserve the three base cases, then append all drilling-wheel-on-ridge
    # orientations.  Valid cases come first; rejected orientations remain
    # selectable as diagnostics so the body interference can be inspected.
    base_cases = list(cases)
    ordered_candidates = sorted(
        rotation9_sweep_candidates,
        key=lambda item: (
            not item["accepted"], item["base_passive_case"], item["rotation9_deg"]
        ),
    )
    for candidate in ordered_candidates:
        if not candidate["on_ridge"]:
            continue
        base_case = base_cases[candidate["base_passive_case"] - 1]
        angle = float(candidate["rotation9_deg"])
        rotated_wheel = rotate_about_pipe_axis(
            drilling[0], rotation9_pivot, angle
        )
        drilling_gap = clearance_mm(
            rotated_wheel,
            tuple(base_case["body_pose_m"][key] for key in ("x", "y", "z")),
            estimation,
        )
        expanded = json.loads(json.dumps(base_case))
        expanded["case"] = len(cases) + 1
        expanded["rotation9_deg"] = angle
        expanded["valid_configuration"] = candidate["accepted"]
        expanded["diagnostic_reason"] = (
            "accepted" if candidate["accepted"]
            else "drilling module body penetrates the pipe before/at wheel contact"
        )
        expanded["drilling_wheel_clearance_mm"] = [drilling_gap]
        expanded["drilling_contact"] = [drilling_gap <= CONTACT_TOLERANCE_MM]
        expanded["drilling_module_after_press"] = {
            "sequence": "checked after drive-wheel first contact",
            "contact": candidate["minimum_module_clearance_mm"] <= CONTACT_TOLERANCE_MM,
            "intended_drilling_wheel_contact": True,
            "forbidden_penetration": not candidate["collision_free"],
            "minimum_clearance_mm": candidate["minimum_module_clearance_mm"],
            "closest_component": candidate["closest_component"],
            "closest_point_world_m": candidate["closest_module_point_world_m"],
            "component_clearance_mm": candidate["component_clearance_mm"],
        }
        cases.append(expanded)

    # Re-seat every enumerated configuration under gravity using all four
    # moving-platform passive wheels plus the drilling-module passive wheel.
    # This replaces the alignment-only pose used to seed each case.
    for case in cases:
        angle = float(case.get("rotation9_deg", 0.0))
        rotated_drilling = [
            rotate_about_pipe_axis(wheel, rotation9_pivot, angle)
            for wheel in drilling
        ]
        rotated_modules = {
            name: rotate_about_pipe_axis(vertices, rotation9_pivot, angle)
            for name, vertices in drilling_modules.items()
        }
        initial_pose = tuple(
            case["body_pose_m"][key] for key in ("x", "y", "z")
        )
        com_local, com_parts = estimate_assembly_com(
            assets, cad_center, rotation9_pivot, angle
        )
        pose, body_roll_deg, settled = settle_under_gravity(
            passive, rotated_drilling, rotated_modules,
            initial_pose, 0.0, com_local, estimation,
        )
        passive_clearances = [
            clearance_mm(
                wheel, pose, estimation, body_roll_deg=body_roll_deg
            )
            for wheel in passive
        ]
        drilling_clearances = [
            clearance_mm(
                wheel, pose, estimation, body_roll_deg=body_roll_deg
            )
            for wheel in rotated_drilling
        ]
        module_details = {
            name: clearance_details(
                vertices, pose, estimation, body_roll_deg
            )
            for name, vertices in rotated_modules.items()
        }
        closest_module, (module_gap, module_point) = min(
            module_details.items(), key=lambda item: item[1][0]
        )
        drive_state = drive_first_contact(drive, pose, estimation, body_roll_deg)
        case["initial_alignment_pose_m"] = case["body_pose_m"]
        case["body_pose_m"] = {
            "x": pose[0], "y": pose[1], "z": pose[2]
        }
        case["body_roll_deg"] = body_roll_deg
        case["gravity_settled"] = {
            **settled,
            "sequence": "all passive wheels enabled before cylinder press",
            "mass_kg": ROBOT_MASS_KG,
            "gravity_m_s2": GRAVITY_M_S2,
            "wheel_radial_stiffness_n_per_mm": WHEEL_RADIAL_STIFFNESS_N_PER_MM,
            "body_contact_stiffness_n_per_mm": BODY_CONTACT_STIFFNESS_N_PER_MM,
            "com_local_sim_m": com_local.tolist(),
            "com_method": "provisional STL enclosed-volume ratio",
            "component_com_estimates": com_parts,
        }
        case["passive_clearance_mm"] = passive_clearances
        case["passive_contact"] = [
            gap <= CONTACT_TOLERANCE_MM for gap in passive_clearances
        ]
        case["drilling_wheel_clearance_mm"] = drilling_clearances
        case["drilling_contact"] = [
            gap <= CONTACT_TOLERANCE_MM for gap in drilling_clearances
        ]
        case["drilling_module_after_press"] = {
            "sequence": "gravity settlement, then drive-wheel first contact",
            "contact": module_gap <= CONTACT_TOLERANCE_MM,
            "intended_drilling_wheel_contact": any(case["drilling_contact"]),
            "forbidden_penetration": module_gap < -CONTACT_TOLERANCE_MM,
            "minimum_clearance_mm": module_gap,
            "closest_component": closest_module,
            "closest_point_world_m": module_point.tolist(),
            "component_clearance_mm": {
                name: detail[0] for name, detail in module_details.items()
            },
        }
        case.update(drive_state)
        if "valid_configuration" in case:
            case["valid_configuration"] = (
                any(case["drilling_contact"])
                and not case["drilling_module_after_press"]["forbidden_penetration"]
            )
            case["diagnostic_reason"] = (
                "accepted after gravity settlement"
                if case["valid_configuration"] else
                "rejected after gravity settlement: no drilling-wheel support or body penetration"
            )
    result = {
        "method": "initial ridge alignment; full passive-wheel gravity settlement; rigid first drive-wheel contact analysis",
        "contact_tolerance_mm": CONTACT_TOLERANCE_MM,
        "maximum_cylinder_stroke_mm": SLIDER6_MAX_MM - SLIDER6_MIN_MM,
        "passive_wheel_order_cad_centers_mm": PASSIVE_WHEELS_CAD_MM.tolist(),
        "drive_wheel_order_cad_centers_mm": DRIVE_WHEELS_MODULE_CAD_MM.tolist(),
        "drilling_wheel_order_cad_centers_mm": DRILLING_WHEELS_MODULE_CAD_MM.tolist(),
        "rotation9_sweep_deg": [ROTATION9_MIN_DEG, ROTATION9_MAX_DEG],
        "ridge_min_height_mm": RIDGE_MIN_HEIGHT_MM,
        "rotation9_contact_candidates": rotation9_sweep_candidates,
        "cases": cases,
    }
    output = here / "output" / "ridge_contact_cases.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"output={output}")


if __name__ == "__main__":
    main()
