#!/usr/bin/env python3
"""Display and collision-test a reconstructed pipe mesh with Bullet physics."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import trimesh
from panda3d.bullet import (
    BulletConvexHullShape,
    BulletRigidBodyNode,
    BulletSphereShape,
    BulletTriangleMesh,
    BulletTriangleMeshShape,
    BulletWorld,
)
from panda3d.core import (
    AmbientLight,
    DirectionalLight,
    Filename,
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    LColor,
    NodePath,
    TextNode,
    TransparencyAttrib,
    Vec3,
    loadPrcFileData,
)


MM_TO_M = 0.001
ROBOT_MODULE_POSITION_MM = np.array([42.0, 0.0, -47.0])
DRILLING1_MODULE_POSITION_MM = np.array([-59.0, 50.0, -373.0])
DRILLING2_MODULE_POSITION_MM = np.array([-100.0, 0.0, -335.0])
BIT_MODULE_POSITION_MM = np.array([-59.0, 116.0, -369.0])
SLIDER6_MIN_MM = 43.0
SLIDER6_MAX_MM = 73.0
SLIDER11_MIN_MM = -121.764
# Deterministic seating poses obtained from the fitted wheel hulls.  The
# support-wheel envelope is placed against a conservative 118.5 mm inner
# radius (120 mm nominal minus the 1.5 mm inferred ridge height).
NORMAL_PASSIVE_SEAT_Y_M = -0.0132433341
NORMAL_PASSIVE_SEAT_Z_M = -0.0026645325
INVERTED_DRIVE_SEAT_Y_M = 0.0138098316
INVERTED_DRIVE_SEAT_Z_M = -0.0109828398
# Wheel centres measured from the Fusion-derived module STL coordinates.
# Four passive casters belong to 01_move_module; four drive wheels belong to
# 02_motor_module.  The simple spherical proxies intentionally model only the
# contact envelope, not the surrounding frames.
PASSIVE_WHEELS_CAD_MM = np.array([
    [-112.29, -119.04, -2.64],
    [-112.29, -119.04, -222.64],
    [-5.37, -119.04, -2.64],
    [-5.37, -119.04, -222.64],
])
DRIVE_WHEELS_MODULE_CAD_MM = np.array([
    [-154.76, 2.03, 103.15],
    [-45.76, 2.03, 103.15],
    [-154.76, 2.03, -191.85],
    [-45.76, 2.03, -191.85],
])
DRILLING_WHEELS_MODULE_CAD_MM = np.array([
    [0.0, -180.01, 34.0],
])
# Values measured from the wheel components contained in the module STLs.
# Wheel axes are CAD X, which maps to simulator Y.


def load_pipe_geom(stl_path: Path) -> tuple[Geom, np.ndarray]:
    mesh = trimesh.load_mesh(stl_path, process=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError(f"Expected one triangular mesh: {stl_path}")
    vertices = np.asarray(mesh.vertices, dtype=float) * MM_TO_M
    normals = np.asarray(mesh.vertex_normals, dtype=float)
    faces = np.asarray(mesh.faces, dtype=np.int32)

    data = GeomVertexData("measured_pipe", GeomVertexFormat.getV3n3(), Geom.UHStatic)
    data.setNumRows(len(vertices))
    vertex_writer = GeomVertexWriter(data, "vertex")
    normal_writer = GeomVertexWriter(data, "normal")
    for vertex, normal in zip(vertices, normals):
        vertex_writer.addData3f(*vertex)
        normal_writer.addData3f(*normal)

    triangles = GeomTriangles(Geom.UHStatic)
    for a, b, c in faces:
        triangles.addVertices(int(a), int(b), int(c))
    triangles.closePrimitive()
    geom = Geom(data)
    geom.addPrimitive(triangles)
    return geom, np.vstack((vertices.min(axis=0), vertices.max(axis=0)))


def make_geom(name: str, vertices: np.ndarray, normals: np.ndarray,
              faces: np.ndarray) -> Geom:
    """Convert triangle arrays to a Panda3D geometry."""
    data = GeomVertexData(name, GeomVertexFormat.getV3n3(), Geom.UHStatic)
    data.setNumRows(len(vertices))
    vertex_writer = GeomVertexWriter(data, "vertex")
    normal_writer = GeomVertexWriter(data, "normal")
    for vertex, normal in zip(vertices, normals):
        vertex_writer.addData3f(*vertex)
        normal_writer.addData3f(*normal)

    triangles = GeomTriangles(Geom.UHStatic)
    for a, b, c in faces:
        triangles.addVertices(int(a), int(b), int(c))
    triangles.closePrimitive()
    geom = Geom(data)
    geom.addPrimitive(triangles)
    return geom


def load_robot_mesh(stl_path: Path) -> trimesh.Trimesh:
    mesh = trimesh.load_mesh(stl_path, process=True)
    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError(f"Expected one triangular mesh: {stl_path}")
    return mesh


def passive_wheel_components(mesh: trimesh.Trimesh) -> list[trimesh.Trimesh]:
    """Extract the four barrel-shaped passive wheels from the module STL."""
    components = mesh.split(only_watertight=False)
    wheels: list[trimesh.Trimesh] = []
    for expected_center in PASSIVE_WHEELS_CAD_MM:
        wheel = min(
            components,
            key=lambda part: np.linalg.norm(
                np.asarray(part.bounding_box.centroid) - expected_center
            ),
        )
        center_error = float(np.linalg.norm(
            np.asarray(wheel.bounding_box.centroid) - expected_center
        ))
        if center_error > 0.25:
            raise RuntimeError(
                f"Passive wheel component fit error {center_error:.3f} mm "
                f"at expected center {expected_center.tolist()}"
            )
        wheels.append(wheel)
    return wheels


def drive_wheel_components(mesh: trimesh.Trimesh) -> list[trimesh.Trimesh]:
    """Extract the four asymmetric half-barrel drive-wheel tires.

    The STL contains a roughly 40 mm-diameter metal wheel at each nominal
    centre and a separate roughly 54 mm-diameter tire.  Some tires are split
    into several adjacent 1--2 mm rings.  Select and concatenate the outer
    rings instead of choosing the closest-centred component (the hub).
    """
    components = mesh.split(only_watertight=False)
    wheels: list[trimesh.Trimesh] = []
    for expected_center in DRIVE_WHEELS_MODULE_CAD_MM:
        tire_parts: list[trimesh.Trimesh] = []
        for part in components:
            center = np.asarray(part.bounding_box.centroid)
            extent = np.asarray(part.bounding_box.extents)
            aligned = (
                abs(center[0] - expected_center[0]) < 20.0
                and abs(center[1] - expected_center[1]) < 0.20
                and abs(center[2] - expected_center[2]) < 0.20
            )
            # Tire axis is CAD X.  Its individual sections are no wider than
            # 13 mm and have an outer Y/Z diameter of at least about 39.8 mm.
            is_outer_tire = (
                extent[0] <= 15.0
                and extent[1] >= 39.5
                and extent[2] >= 39.5
            )
            if aligned and is_outer_tire:
                tire_parts.append(part)
        if not tire_parts:
            raise RuntimeError(
                f"Drive tire components not found at {expected_center.tolist()}"
            )
        tire = trimesh.util.concatenate(tire_parts)
        diameter = max(tire.bounding_box.extents[1:])
        if not 50.0 <= diameter <= 56.0:
            raise RuntimeError(
                f"Unexpected drive tire diameter {diameter:.3f} mm "
                f"at {expected_center.tolist()}"
            )
        wheels.append(tire)
    return wheels


def drilling_wheel_components(mesh: trimesh.Trimesh) -> list[trimesh.Trimesh]:
    """Extract the 16 mm-wide, approximately 32 mm drilling-module wheel."""
    components = mesh.split(only_watertight=False)
    wheels: list[trimesh.Trimesh] = []
    for expected_center in DRILLING_WHEELS_MODULE_CAD_MM:
        wheel = min(
            components,
            key=lambda part: np.linalg.norm(
                np.asarray(part.bounding_box.centroid) - expected_center
            ),
        )
        center_error = float(np.linalg.norm(
            np.asarray(wheel.bounding_box.centroid) - expected_center
        ))
        if center_error > 0.25:
            raise RuntimeError(
                f"Drilling wheel component fit error {center_error:.3f} mm "
                f"at expected center {expected_center.tolist()}"
            )
        wheels.append(wheel)
    return wheels


def cad_to_sim(values: np.ndarray) -> np.ndarray:
    """Map Fusion/Inventor XYZ to simulator XYZ (pipe axis is CAD Z)."""
    return np.asarray(values, dtype=float)[..., [2, 0, 1]] * MM_TO_M


def robot_geom(mesh: trimesh.Trimesh, cad_offset_mm: np.ndarray,
               cad_center_mm: np.ndarray, name: str) -> Geom:
    vertices = cad_to_sim(np.asarray(mesh.vertices) + cad_offset_mm - cad_center_mm)
    # Axis permutation has determinant +1, so winding and normals remain valid.
    normals = np.asarray(mesh.vertex_normals, dtype=float)[..., [2, 0, 1]]
    return make_geom(name, vertices, normals, np.asarray(mesh.faces, dtype=np.int32))


def transformed_robot_geom(mesh: trimesh.Trimesh, cad_offset_mm: np.ndarray,
                           cad_center_mm: np.ndarray, name: str,
                           rpy_deg: tuple[float, float, float] = (0, 0, 0)) -> Geom:
    """Apply the validated Fusion module pose before CAD-to-simulator mapping."""
    rotation = trimesh.transformations.euler_matrix(
        *np.radians(rpy_deg), axes="sxyz"
    )[:3, :3]
    vertices_cad = np.asarray(mesh.vertices) @ rotation.T + cad_offset_mm
    normals_cad = np.asarray(mesh.vertex_normals) @ rotation.T
    vertices = cad_to_sim(vertices_cad - cad_center_mm)
    normals = normals_cad[..., [2, 0, 1]]
    return make_geom(name, vertices, normals, np.asarray(mesh.faces, dtype=np.int32))


def create_world(stl_path: Path) -> tuple[BulletWorld, NodePath, NodePath, np.ndarray]:
    world = BulletWorld()
    world.setGravity(Vec3(0.0, 0.0, -9.80665))
    geom, bounds = load_pipe_geom(stl_path)

    triangle_mesh = BulletTriangleMesh()
    triangle_mesh.addGeom(geom)
    pipe_shape = BulletTriangleMeshShape(triangle_mesh, dynamic=False)
    pipe_node = BulletRigidBodyNode("measured_pipe_static")
    pipe_node.addShape(pipe_shape)
    pipe_np = NodePath(pipe_node)
    world.attachRigidBody(pipe_node)

    sphere_node = BulletRigidBodyNode("collision_test_sphere")
    sphere_node.setMass(0.20)
    sphere_node.addShape(BulletSphereShape(0.015))
    sphere_np = NodePath(sphere_node)
    center_x = float((bounds[0, 0] + bounds[1, 0]) * 0.5)
    sphere_np.setPos(center_x, 0.0, 0.02)
    world.attachRigidBody(sphere_node)
    return world, pipe_np, sphere_np, bounds


def run_headless(stl_path: Path) -> int:
    world, _pipe_np, sphere_np, bounds = create_world(stl_path)
    for _ in range(720):
        world.doPhysics(1.0 / 240.0, 1, 1.0 / 240.0)
    contacts = world.contactTest(sphere_np.node()).getNumContacts()
    position = sphere_np.getPos()
    print(f"pipe_bounds_m={bounds.tolist()}")
    print(f"sphere_final_m={[position.x, position.y, position.z]}")
    print(f"sphere_contacts={contacts}")
    # A 15 mm sphere dropped in a roughly 120 mm inner radius should stop near z=-0.105 m.
    passed = contacts > 0 and -0.120 < position.z < -0.085
    print(f"collision_test={'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


class MeasuredPipeApp:
    def __init__(self, stl_path: Path, robot_assets: Path,
                 offscreen: bool = False) -> None:
        loadPrcFileData("", "window-title LK-G85A estimated full-pipe simulator")
        loadPrcFileData("", "win-size 1400 900")
        loadPrcFileData("", "win-origin 40 40")
        loadPrcFileData("", "fullscreen false")
        loadPrcFileData("", "show-frame-rate-meter true")
        if offscreen:
            loadPrcFileData("", "window-type offscreen")
        from direct.showbase.ShowBase import ShowBase
        from direct.gui.OnscreenText import OnscreenText

        self.base = ShowBase()
        self.base.setBackgroundColor(0.025, 0.035, 0.055, 1.0)
        self.world, pipe_physics_np, self.sphere_np, self.bounds = create_world(stl_path)

        # Reuse the exact same triangles for visible geometry and collision geometry.
        geom, _ = load_pipe_geom(stl_path)
        visual_node = GeomNode("measured_pipe_visual")
        visual_node.addGeom(geom)
        visual_np = self.base.render.attachNewNode(visual_node)
        visual_np.setTwoSided(True)
        # Keep both the outer wall and inner wall visible while allowing the
        # robot and contact markers to be inspected through the full pipe.
        visual_np.setTransparency(TransparencyAttrib.MAlpha)
        visual_np.setColor(0.10, 0.52, 0.82, 0.18)
        visual_np.setDepthWrite(False)
        visual_np.setBin("transparent", 20)
        pipe_physics_np.reparentTo(self.base.render)

        sphere_visual = self.base.loader.loadModel("models/misc/sphere")
        sphere_visual.reparentTo(self.sphere_np)
        sphere_visual.setScale(0.015)
        sphere_visual.setColor(1.0, 0.25, 0.1, 1.0)
        self.sphere_np.reparentTo(self.base.render)
        # Keep the old collision-test ball available, but do not let it obscure
        # the robot during normal inspection.
        self.sphere_np.hide()

        self.slider6_mm = SLIDER6_MIN_MM
        self.robot_inverted = False
        contact_result_path = Path(__file__).resolve().parent / "output" / "ridge_contact_cases.json"
        if not contact_result_path.exists():
            raise FileNotFoundError(
                f"Ridge contact cases not found: {contact_result_path}. "
                "Run analyze_ridge_contact_cases.py first."
            )
        self.ridge_contact_result = json.loads(
            contact_result_path.read_text(encoding="utf-8")
        )
        self.cylinder_pressed = False
        self.robot_body = BulletRigidBodyNode("moving_platform_contact_body")
        self.robot_body.setMass(21.0)
        self.robot_body.setFriction(0.55)
        self.robot_body.setLinearDamping(0.35)
        self.robot_body.setAngularDamping(0.65)
        # Keep vertical seating and roll about the pipe axis; axial travel is
        # applied explicitly with the arrow keys for repeatable inspection.
        self.robot_body.setLinearFactor(Vec3(0.0, 0.0, 1.0))
        self.robot_body.setAngularFactor(Vec3(1.0, 0.0, 0.0))
        self.robot_root = self.base.render.attachNewNode(self.robot_body)
        self.robot_visual_root = self.robot_root.attachNewNode("moving_platform_visual")
        self.world.attachRigidBody(self.robot_body)
        self.drive_contact_shapes: list[BulletConvexHullShape] = []
        self.drive_wheel_meshes: list[trimesh.Trimesh] = []
        self.passive_contact_markers: list[NodePath] = []
        self.drive_contact_markers: list[NodePath] = []
        self.drilling_contact_markers: list[NodePath] = []
        self.motor_visual: NodePath | None = None
        self.load_robot(robot_assets)
        self.contact_case = 1
        self.contact_case_label = "passive wheel 1 on ridge / full robot"

        ambient = AmbientLight("ambient")
        ambient.setColor(LColor(0.55, 0.55, 0.55, 1.0))
        self.base.render.setLight(self.base.render.attachNewNode(ambient))
        sun = DirectionalLight("sun")
        sun.setColor(LColor(0.8, 0.8, 0.8, 1.0))
        sun_np = self.base.render.attachNewNode(sun)
        sun_np.setHpr(-35, -45, 0)
        self.base.render.setLight(sun_np)

        center_x = float((self.bounds[0, 0] + self.bounds[1, 0]) * 0.5)
        self.camera_target = Vec3(center_x - 0.16, 0.0, 0.0)
        self.camera_heading_deg = -90.0
        self.camera_pitch_deg = 22.0
        self.camera_distance_m = 1.08
        self.camera_drag_mode: str | None = None
        self.camera_last_mouse: tuple[float, float] | None = None
        # The model is less than one metre from the camera.  Panda3D's default
        # near plane can clip the complete scene unless it is reduced explicitly.
        self.base.camLens.setNearFar(0.01, 10.0)
        self.base.camLens.setFov(48.0)
        self.base.disableMouse()
        self.update_camera_pose()

        self.base.accept("escape", self.base.userExit)
        self.base.accept("space", self.reset_sphere)
        self.base.accept("1", self.apply_contact_case, [1])
        self.base.accept("2", self.apply_contact_case, [2])
        self.base.accept("3", self.apply_contact_case, [3])
        self.base.accept("c", self.toggle_cylinder_press)
        self.base.accept("r", self.reset_robot)
        self.base.accept("mouse1", self.begin_camera_drag, ["orbit"])
        self.base.accept("mouse1-up", self.end_camera_drag)
        self.base.accept("mouse3", self.begin_camera_drag, ["pan"])
        self.base.accept("mouse3-up", self.end_camera_drag)
        self.base.accept("wheel_up", self.zoom_camera, [0.88])
        self.base.accept("wheel_down", self.zoom_camera, [1.14])
        self.base.accept("a", self.orbit_camera, [-5.0, 0.0])
        self.base.accept("d", self.orbit_camera, [5.0, 0.0])
        self.base.accept("w", self.orbit_camera, [0.0, 5.0])
        self.base.accept("s", self.orbit_camera, [0.0, -5.0])
        self.base.accept("q", self.zoom_camera, [1.10])
        self.base.accept("e", self.zoom_camera, [0.90])
        OnscreenText(
            text=("Estimated full pipe + moving platform / X: pipe axis\n"
                  "Mouse L-drag: orbit  R-drag: pan  Wheel: zoom  A/D/W/S/Q/E: camera\n"
                  "1: wheel 1  2: wheels 2+3  3: wheel 4 on ridge  C: cylinder press/retract\n"
                  "Red filled: contact  Red wireframe: non-contact proxy  R: reset case  Esc: exit"),
            pos=(-1.28, 0.92), align=TextNode.ALeft, scale=0.045,
            fg=(1, 1, 1, 1), shadow=(0, 0, 0, 0.8), mayChange=False,
        )
        self.robot_status = OnscreenText(
            text="", pos=(-1.28, 0.69), align=TextNode.ALeft, scale=0.040,
            fg=(1.0, 0.85, 0.35, 1.0), shadow=(0, 0, 0, 0.8), mayChange=True,
        )
        self.apply_contact_case(1)
        self.base.taskMgr.add(self.update, "bullet_update")

    def update_camera_pose(self) -> None:
        heading = np.radians(self.camera_heading_deg)
        pitch = np.radians(self.camera_pitch_deg)
        horizontal = self.camera_distance_m * np.cos(pitch)
        offset = Vec3(
            horizontal * np.cos(heading),
            horizontal * np.sin(heading),
            self.camera_distance_m * np.sin(pitch),
        )
        self.base.camera.setPos(self.camera_target + offset)
        self.base.camera.lookAt(self.camera_target)

    def begin_camera_drag(self, mode: str) -> None:
        if not self.base.mouseWatcherNode.hasMouse():
            return
        mouse = self.base.mouseWatcherNode.getMouse()
        self.camera_drag_mode = mode
        self.camera_last_mouse = (float(mouse.x), float(mouse.y))

    def end_camera_drag(self) -> None:
        self.camera_drag_mode = None
        self.camera_last_mouse = None

    def update_camera_drag(self) -> None:
        if self.camera_drag_mode is None or not self.base.mouseWatcherNode.hasMouse():
            return
        mouse = self.base.mouseWatcherNode.getMouse()
        current = (float(mouse.x), float(mouse.y))
        if self.camera_last_mouse is None:
            self.camera_last_mouse = current
            return
        dx = current[0] - self.camera_last_mouse[0]
        dy = current[1] - self.camera_last_mouse[1]
        self.camera_last_mouse = current
        if self.camera_drag_mode == "orbit":
            self.camera_heading_deg -= dx * 140.0
            self.camera_pitch_deg = float(np.clip(
                self.camera_pitch_deg + dy * 110.0, -85.0, 85.0
            ))
        else:
            self.camera_target.x -= dx * self.camera_distance_m * 0.65
            self.camera_target.z -= dy * self.camera_distance_m * 0.65
        self.update_camera_pose()

    def orbit_camera(self, heading_delta: float, pitch_delta: float) -> None:
        self.camera_heading_deg += heading_delta
        self.camera_pitch_deg = float(np.clip(
            self.camera_pitch_deg + pitch_delta, -85.0, 85.0
        ))
        self.update_camera_pose()

    def zoom_camera(self, factor: float) -> None:
        self.camera_distance_m = float(np.clip(
            self.camera_distance_m * factor, 0.25, 3.0
        ))
        self.update_camera_pose()

    def load_robot(self, robot_assets: Path) -> None:
        base_path = robot_assets / "01_move_module.stl"
        motor_path = robot_assets / "02_motor_module.stl"
        drilling1_path = robot_assets / "03_drilling_module1.stl"
        drilling2_path = robot_assets / "03_drilling_module2.stl"
        bit_path = robot_assets / "04_bit.stl"
        for path in (base_path, motor_path, drilling1_path, drilling2_path, bit_path):
            if not path.exists():
                raise FileNotFoundError(f"Robot module not found: {path}")

        base_mesh = load_robot_mesh(base_path)
        motor_mesh = load_robot_mesh(motor_path)
        drilling1_mesh = load_robot_mesh(drilling1_path)
        drilling2_mesh = load_robot_mesh(drilling2_path)
        bit_mesh = load_robot_mesh(bit_path)
        passive_wheels = passive_wheel_components(base_mesh)
        self.drive_wheel_meshes = drive_wheel_components(motor_mesh)
        drilling_wheels = drilling_wheel_components(drilling1_mesh)
        base_offset = np.zeros(3)
        motor_at_min_offset = ROBOT_MODULE_POSITION_MM + np.array(
            [0.0, SLIDER6_MIN_MM, 0.0]
        )
        combined_min = np.minimum(
            np.asarray(base_mesh.bounds[0]) + base_offset,
            np.asarray(motor_mesh.bounds[0]) + motor_at_min_offset,
        )
        combined_max = np.maximum(
            np.asarray(base_mesh.bounds[1]) + base_offset,
            np.asarray(motor_mesh.bounds[1]) + motor_at_min_offset,
        )
        cad_center = (combined_min + combined_max) * 0.5

        base_node = GeomNode("01_move_module_visual")
        base_node.addGeom(robot_geom(base_mesh, base_offset, cad_center, "move_module"))
        base_visual = self.robot_visual_root.attachNewNode(base_node)
        base_visual.setTwoSided(True)
        base_visual.setColor(0.78, 0.80, 0.82, 1.0)

        motor_node = GeomNode("02_motor_module_visual")
        motor_node.addGeom(robot_geom(
            motor_mesh, motor_at_min_offset, cad_center, "motor_module"
        ))
        self.motor_visual = self.robot_visual_root.attachNewNode(motor_node)
        self.motor_visual.setTwoSided(True)
        self.motor_visual.setColor(0.94, 0.62, 0.16, 1.0)

        # Add the complete drilling assembly at the validated Fusion default
        # joint state: rotation9=0 deg, slider11=min, rotation12=0 deg.
        drilling1_node = GeomNode("03_drilling_module1_visual")
        drilling1_node.addGeom(transformed_robot_geom(
            drilling1_mesh, DRILLING1_MODULE_POSITION_MM, cad_center,
            "drilling_module1",
        ))
        drilling1_visual = self.robot_visual_root.attachNewNode(drilling1_node)
        drilling1_visual.setTwoSided(True)
        drilling1_visual.setColor(0.72, 0.75, 0.79, 1.0)

        drilling2_offset = DRILLING2_MODULE_POSITION_MM + np.array(
            [0.0, SLIDER11_MIN_MM, 0.0]
        )
        drilling2_node = GeomNode("03_drilling_module2_visual")
        drilling2_node.addGeom(transformed_robot_geom(
            drilling2_mesh, drilling2_offset, cad_center, "drilling_module2",
        ))
        drilling2_visual = self.robot_visual_root.attachNewNode(drilling2_node)
        drilling2_visual.setTwoSided(True)
        drilling2_visual.setColor(0.32, 0.64, 0.82, 1.0)

        bit_offset = BIT_MODULE_POSITION_MM + np.array(
            [0.0, SLIDER11_MIN_MM, 0.0]
        )
        bit_node = GeomNode("04_bit_visual")
        bit_node.addGeom(transformed_robot_geom(
            bit_mesh, bit_offset, cad_center, "bit", (90.0, 0.0, 0.0)
        ))
        bit_visual = self.robot_visual_root.attachNewNode(bit_node)
        bit_visual.setTwoSided(True)
        bit_visual.setColor(0.86, 0.24, 0.18, 1.0)

        # Draw the exact proxy volumes used by Bullet in translucent red so
        # contact judgement can be checked against the CAD wheels visually.
        for wheel_index, wheel_mesh in enumerate(passive_wheels, start=1):
            marker = self.add_mesh_contact_marker(
                self.robot_visual_root, wheel_mesh, np.zeros(3), cad_center,
                f"passive_wheel_{wheel_index}_contact_proxy",
            )
            self.passive_contact_markers.append(marker)
        for wheel_index, wheel_mesh in enumerate(
            self.drive_wheel_meshes, start=1
        ):
            marker = self.add_mesh_contact_marker(
                self.motor_visual, wheel_mesh, motor_at_min_offset, cad_center,
                f"drive_wheel_{wheel_index}_contact_proxy",
            )
            self.drive_contact_markers.append(marker)
        for wheel_index, wheel_mesh in enumerate(drilling_wheels, start=1):
            marker = self.add_mesh_contact_marker(
                self.robot_visual_root, wheel_mesh,
                DRILLING1_MODULE_POSITION_MM, cad_center,
                f"drilling_wheel_{wheel_index}_contact_proxy",
            )
            self.drilling_contact_markers.append(marker)

        center_x = float((self.bounds[0, 0] + self.bounds[1, 0]) * 0.5)
        self.robot_root.setPos(center_x, 0.0, 0.0)
        self.robot_cad_center_mm = cad_center
        for wheel_mesh in passive_wheels:
            wheel_hull = wheel_mesh.convex_hull
            local_vertices = cad_to_sim(
                np.asarray(wheel_hull.vertices) - cad_center
            )
            shape = BulletConvexHullShape()
            for vertex in local_vertices:
                shape.addPoint(Vec3(*vertex.tolist()))
            shape.setMargin(0.0002)
            self.robot_body.addShape(shape)
        for wheel_mesh in drilling_wheels:
            wheel_hull = wheel_mesh.convex_hull
            local_vertices = cad_to_sim(
                np.asarray(wheel_hull.vertices)
                + DRILLING1_MODULE_POSITION_MM - cad_center
            )
            shape = BulletConvexHullShape()
            for vertex in local_vertices:
                shape.addPoint(Vec3(*vertex.tolist()))
            shape.setMargin(0.0002)
            self.robot_body.addShape(shape)
        self.rebuild_drive_contact_shapes()

    def add_mesh_contact_marker(self, parent: NodePath,
                                wheel_mesh: trimesh.Trimesh,
                                cad_offset_mm: np.ndarray,
                                cad_center: np.ndarray, name: str) -> NodePath:
        """Overlay an exact wheel mesh as a red wireframe."""
        marker_node = GeomNode(name)
        marker_node.addGeom(robot_geom(
            wheel_mesh, cad_offset_mm, cad_center, name
        ))
        marker = parent.attachNewNode(marker_node)
        marker.setTwoSided(True)
        marker.setTransparency(TransparencyAttrib.MAlpha)
        marker.setColor(0.42, 0.46, 0.52, 0.38)
        marker.setRenderModeWireframe()
        marker.setRenderModeThickness(2.0)
        marker.setDepthOffset(2)
        return marker

    def rebuild_drive_contact_shapes(self) -> None:
        for shape in self.drive_contact_shapes:
            self.robot_body.removeShape(shape)
        self.drive_contact_shapes.clear()
        offset = ROBOT_MODULE_POSITION_MM + np.array([0.0, self.slider6_mm, 0.0])
        for wheel_mesh in self.drive_wheel_meshes:
            wheel_hull = wheel_mesh.convex_hull
            local_vertices = cad_to_sim(
                np.asarray(wheel_hull.vertices) + offset
                - self.robot_cad_center_mm
            )
            shape = BulletConvexHullShape()
            for vertex in local_vertices:
                shape.addPoint(Vec3(*vertex.tolist()))
            shape.setMargin(0.0002)
            self.robot_body.addShape(
                shape
            )
            self.drive_contact_shapes.append(shape)
        self.robot_body.setActive(True)

    def update_robot_status(self) -> None:
        case = self.ridge_contact_result["cases"][self.contact_case - 1]
        passive = [index + 1 for index, contact in enumerate(
            case["passive_contact"]
        ) if contact]
        drive = [index + 1 for index, contact in enumerate(
            case["drive_contact"]
        ) if contact] if self.cylinder_pressed else []
        drilling = [index + 1 for index, contact in enumerate(
            case["drilling_contact"]
        ) if contact]
        drilling_gap = min(case["drilling_wheel_clearance_mm"])
        state = "pressed to first drive contact" if self.cylinder_pressed else "retracted"
        self.robot_status.setText(
            f"Case {self.contact_case}: {self.contact_case_label}\n"
            f"Cylinder: {state} / slider {self.slider6_mm:.2f} mm\n"
            f"Passive: {passive or 'none'} / Drive: {drive or 'none'}"
            f" / Drilling: {drilling or 'none'} (gap {drilling_gap:.2f} mm)"
        )

    @staticmethod
    def set_contact_marker(marker: NodePath, contact: bool) -> None:
        if contact:
            marker.setColor(1.0, 0.02, 0.02, 0.96)
            marker.setRenderModeFilled()
            marker.setDepthTest(False)
            marker.setBin("fixed", 100)
        else:
            # Keep every collision proxy visible.  Contact state is encoded by
            # filled versus wireframe rendering rather than hiding or graying
            # non-contact wheels, so all fitted wheel locations remain auditable.
            marker.setColor(1.0, 0.02, 0.02, 0.86)
            marker.setRenderModeWireframe()
            marker.setRenderModeThickness(3.0)
            marker.setDepthTest(False)
            marker.setBin("fixed", 90)

    def update_contact_marker_colors(self, case: dict) -> None:
        for marker, contact in zip(
            self.passive_contact_markers, case["passive_contact"]
        ):
            self.set_contact_marker(marker, bool(contact))
        drive_contacts = case["drive_contact"] if self.cylinder_pressed else [False] * 4
        for marker, contact in zip(self.drive_contact_markers, drive_contacts):
            self.set_contact_marker(marker, bool(contact))
        for marker, contact in zip(
            self.drilling_contact_markers, case["drilling_contact"]
        ):
            self.set_contact_marker(marker, bool(contact))

    def apply_contact_case(self, case_number: int) -> None:
        """Place one selected passive wheel on the inferred helical ridge."""
        if not 1 <= case_number <= len(self.ridge_contact_result["cases"]):
            raise ValueError(f"Unknown contact case: {case_number}")
        case = self.ridge_contact_result["cases"][case_number - 1]
        pose = case["body_pose_m"]
        self.contact_case = case_number
        wheels = case["passive_wheels_on_ridge"]
        wheel_text = "+".join(str(number) for number in wheels)
        self.contact_case_label = f"passive wheel {wheel_text} on ridge / full robot"
        self.robot_inverted = False
        self.cylinder_pressed = False
        self.slider6_mm = SLIDER6_MIN_MM
        if self.motor_visual is not None:
            self.motor_visual.setZ(0.0)
        self.rebuild_drive_contact_shapes()
        self.robot_root.setPos(pose["x"], pose["y"], pose["z"])
        self.robot_root.setHpr(0.0, 0.0, 0.0)
        self.robot_body.setLinearVelocity(Vec3(0, 0, 0))
        self.robot_body.setAngularVelocity(Vec3(0, 0, 0))
        self.robot_body.clearForces()
        self.robot_body.setActive(False)
        self.update_contact_marker_colors(case)
        if hasattr(self, "robot_status"):
            self.update_robot_status()

    def toggle_cylinder_press(self) -> None:
        case = self.ridge_contact_result["cases"][self.contact_case - 1]
        self.cylinder_pressed = not self.cylinder_pressed
        stroke = case["cylinder_first_contact_stroke_mm"]
        if self.cylinder_pressed:
            self.slider6_mm = (
                SLIDER6_MAX_MM if stroke is None else SLIDER6_MIN_MM + float(stroke)
            )
        else:
            self.slider6_mm = SLIDER6_MIN_MM
        if self.motor_visual is not None:
            self.motor_visual.setZ((self.slider6_mm - SLIDER6_MIN_MM) * MM_TO_M)
        self.rebuild_drive_contact_shapes()
        self.robot_body.setActive(False)
        self.update_contact_marker_colors(case)
        self.update_robot_status()

    def change_slider6(self, delta_mm: float) -> None:
        self.contact_case = 0
        self.contact_case_label = "manual fixed pose"
        self.slider6_mm = float(np.clip(
            self.slider6_mm + delta_mm, SLIDER6_MIN_MM, SLIDER6_MAX_MM
        ))
        if self.motor_visual is not None:
            # CAD +Y maps to simulator +Z.
            self.motor_visual.setZ((self.slider6_mm - SLIDER6_MIN_MM) * MM_TO_M)
        self.rebuild_drive_contact_shapes()
        self.update_robot_status()

    def move_robot(self, delta_m: float) -> None:
        next_x = float(np.clip(
            self.robot_root.getX() + delta_m,
            self.bounds[0, 0] + 0.20,
            self.bounds[1, 0] - 0.20,
        ))
        self.robot_root.setX(next_x)
        self.robot_body.setLinearVelocity(Vec3(0, 0, 0))
        self.robot_body.setActive(True)

    def reset_robot(self) -> None:
        self.apply_contact_case(self.contact_case if self.contact_case in (1, 2, 3) else 1)

    def reset_sphere(self) -> None:
        center_x = float((self.bounds[0, 0] + self.bounds[1, 0]) * 0.5)
        self.sphere_np.setPos(center_x, 0.0, 0.02)
        self.sphere_np.node().setLinearVelocity(Vec3(0, 0, 0))
        self.sphere_np.node().setAngularVelocity(Vec3(0, 0, 0))
        self.sphere_np.node().setActive(True)
        self.sphere_np.show()

    def update(self, task):
        self.update_camera_drag()
        # Contact inspection is deliberately quasi-static.  Bullet shapes are
        # retained for overlap queries, but gravity integration is disabled so
        # contact cases do not oscillate between triangle facets.
        return task.cont

    def run(self) -> None:
        self.base.run()

    def capture(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(5):
            self.base.graphicsEngine.renderFrame()
        if not self.base.win.saveScreenshot(Filename.fromOsSpecific(str(path))):
            raise RuntimeError(f"Failed to save simulator screenshot: {path}")
        print(f"screenshot={path}")

    def capture_fit_views(self, output_dir: Path) -> None:
        """Capture three views for all wheel proxy/CAD alignment QA."""
        output_dir.mkdir(parents=True, exist_ok=True)
        for name, heading, pitch, distance in (
            ("side", -90.0, 12.0, 0.81),
            ("oblique", -48.0, 18.0, 0.88),
            ("end", 0.0, 0.0, 0.72),
        ):
            self.camera_heading_deg = heading
            self.camera_pitch_deg = pitch
            self.camera_distance_m = distance
            self.update_camera_pose()
            self.capture(output_dir / f"wheel_mesh_fit_{name}.png")

    def capture_contact_cases(self, output_dir: Path) -> None:
        """Capture retracted and first-contact state for all four ridge cases."""
        output_dir.mkdir(parents=True, exist_ok=True)
        self.camera_heading_deg = -52.0
        self.camera_pitch_deg = 12.0
        self.camera_distance_m = 1.24
        self.camera_target.x = float(
            (self.bounds[0, 0] + self.bounds[1, 0]) * 0.5 - 0.14
        )
        self.update_camera_pose()
        for case_number in range(1, 4):
            self.apply_contact_case(case_number)
            self.capture(output_dir / f"ridge_case_{case_number}_retracted.png")
            self.toggle_cylinder_press()
            self.capture(output_dir / f"ridge_case_{case_number}_pressed.png")


def main() -> None:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mesh", type=Path,
        default=here / "output" / "pipe_wall_5mm_full360_helix_estimated.stl",
        help="Reconstructed pipe wall STL (millimetre units).",
    )
    parser.add_argument(
        "--robot-assets", type=Path,
        default=here.parent.parent / "raspi_tools" / "camera_stream" / "assets",
        help="Directory containing 01_move_module.stl and 02_motor_module.stl.",
    )
    parser.add_argument("--headless-test", action="store_true")
    parser.add_argument("--capture-preview", type=Path)
    parser.add_argument("--capture-fit-views", type=Path)
    parser.add_argument("--capture-contact-cases", type=Path)
    args = parser.parse_args()
    mesh = args.mesh.resolve()
    if not mesh.exists():
        raise SystemExit(f"Mesh not found: {mesh}. Run build_pipe_model.py first.")
    if args.headless_test:
        raise SystemExit(run_headless(mesh))
    if args.capture_preview or args.capture_fit_views or args.capture_contact_cases:
        app = MeasuredPipeApp(mesh, args.robot_assets.resolve(), offscreen=True)
        if args.capture_preview:
            app.capture(args.capture_preview.resolve())
        if args.capture_fit_views:
            app.capture_fit_views(args.capture_fit_views.resolve())
        if args.capture_contact_cases:
            app.capture_contact_cases(args.capture_contact_cases.resolve())
        return
    MeasuredPipeApp(mesh, args.robot_assets.resolve()).run()


if __name__ == "__main__":
    main()
