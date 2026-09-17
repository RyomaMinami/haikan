#!/usr/bin/env python3
"""Display and collision-test a reconstructed pipe mesh with Bullet physics."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import trimesh
from panda3d.bullet import (
    BulletCylinderShape,
    BulletRigidBodyNode,
    BulletSphereShape,
    BulletTriangleMesh,
    BulletTriangleMeshShape,
    BulletWorld,
    YUp,
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
    Quat,
    TextNode,
    TransformState,
    TransparencyAttrib,
    Vec3,
    loadPrcFileData,
)


MM_TO_M = 0.001
ROBOT_MODULE_POSITION_MM = np.array([42.0, 0.0, -47.0])
SLIDER6_MIN_MM = 43.0
SLIDER6_MAX_MM = 73.0
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
PASSIVE_WHEEL_AXES_CAD = np.array([
    [-0.8660254, 0.5, 0.0],
    [-0.8660254, 0.5, 0.0],
    [-0.8660254, -0.5, 0.0],
    [-0.8660254, -0.5, 0.0],
])
DRIVE_WHEELS_MODULE_CAD_MM = np.array([
    [-154.76, 2.03, 103.15],
    [-45.76, 2.03, 103.15],
    [-154.76, 2.03, -191.85],
    [-45.76, 2.03, -191.85],
])
# Values measured from the wheel components contained in the module STLs.
# Wheel axes are CAD X, which maps to simulator Y.
# Principal-axis fitting of the wheel component gives a 24.94 mm diameter and
# a 15.00 mm axial width.  The axes are mirrored by 30 degrees left/right.
PASSIVE_WHEEL_CONTACT_RADIUS_MM = 12.47
PASSIVE_WHEEL_CONTACT_WIDTH_MM = 15.0
DRIVE_WHEEL_CONTACT_RADIUS_MM = 20.0
DRIVE_WHEEL_CONTACT_WIDTH_MM = 19.0


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


def cad_to_sim(values: np.ndarray) -> np.ndarray:
    """Map Fusion/Inventor XYZ to simulator XYZ (pipe axis is CAD Z)."""
    return np.asarray(values, dtype=float)[..., [2, 0, 1]] * MM_TO_M


def cad_direction_to_sim(values: np.ndarray) -> np.ndarray:
    """Map a CAD direction to simulator axes without applying unit scaling."""
    direction = np.asarray(values, dtype=float)[..., [2, 0, 1]]
    return direction / np.linalg.norm(direction, axis=-1, keepdims=True)


def quaternion_from_y_axis(direction: np.ndarray) -> Quat:
    """Return a quaternion rotating a Y-axis cylinder onto direction."""
    target = Vec3(*(np.asarray(direction, dtype=float) / np.linalg.norm(direction)))
    source = Vec3(0.0, 1.0, 0.0)
    rotation_axis = source.cross(target)
    dot = float(np.clip(source.dot(target), -1.0, 1.0))
    angle_deg = float(np.degrees(np.arccos(dot)))
    quaternion = Quat()
    if rotation_axis.lengthSquared() < 1e-12:
        quaternion.setFromAxisAngle(0.0 if dot > 0 else 180.0, Vec3(1, 0, 0))
    else:
        rotation_axis.normalize()
        quaternion.setFromAxisAngle(angle_deg, rotation_axis)
    return quaternion


def robot_geom(mesh: trimesh.Trimesh, cad_offset_mm: np.ndarray,
               cad_center_mm: np.ndarray, name: str) -> Geom:
    vertices = cad_to_sim(np.asarray(mesh.vertices) + cad_offset_mm - cad_center_mm)
    # Axis permutation has determinant +1, so winding and normals remain valid.
    normals = np.asarray(mesh.vertex_normals, dtype=float)[..., [2, 0, 1]]
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
        self.drive_contact_shapes: list[BulletCylinderShape] = []
        self.motor_visual: NodePath | None = None
        self.load_robot(robot_assets)

        ambient = AmbientLight("ambient")
        ambient.setColor(LColor(0.55, 0.55, 0.55, 1.0))
        self.base.render.setLight(self.base.render.attachNewNode(ambient))
        sun = DirectionalLight("sun")
        sun.setColor(LColor(0.8, 0.8, 0.8, 1.0))
        sun_np = self.base.render.attachNewNode(sun)
        sun_np.setHpr(-35, -45, 0)
        self.base.render.setLight(sun_np)

        center_x = float((self.bounds[0, 0] + self.bounds[1, 0]) * 0.5)
        self.camera_target = Vec3(center_x, 0.0, 0.0)
        self.camera_heading_deg = -90.0
        self.camera_pitch_deg = 22.0
        self.camera_distance_m = 0.81
        self.camera_drag_mode: str | None = None
        self.camera_last_mouse: tuple[float, float] | None = None
        # The model is less than one metre from the camera.  Panda3D's default
        # near plane can clip the complete scene unless it is reduced explicitly.
        self.base.camLens.setNearFar(0.01, 10.0)
        self.base.camLens.setFov(48.0)
        self.base.disableMouse()
        self.update_camera_pose()

        self.paused = False
        self.base.accept("escape", self.base.userExit)
        self.base.accept("space", self.reset_sphere)
        self.base.accept("p", self.toggle_pause)
        self.base.accept("[", self.change_slider6, [-1.0])
        self.base.accept("[-repeat", self.change_slider6, [-1.0])
        self.base.accept("]", self.change_slider6, [1.0])
        self.base.accept("]-repeat", self.change_slider6, [1.0])
        self.base.accept("arrow_left", self.move_robot, [-0.005])
        self.base.accept("arrow_left-repeat", self.move_robot, [-0.005])
        self.base.accept("arrow_right", self.move_robot, [0.005])
        self.base.accept("arrow_right-repeat", self.move_robot, [0.005])
        self.base.accept("i", self.toggle_robot_inversion)
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
                  "Left/Right: travel  [ / ]: cylinder  I: invert  R: reset/settle  Esc: exit"),
            pos=(-1.28, 0.92), align=TextNode.ALeft, scale=0.045,
            fg=(1, 1, 1, 1), shadow=(0, 0, 0, 0.8), mayChange=False,
        )
        self.robot_status = OnscreenText(
            text="", pos=(-1.28, 0.74), align=TextNode.ALeft, scale=0.040,
            fg=(1.0, 0.85, 0.35, 1.0), shadow=(0, 0, 0, 0.8), mayChange=True,
        )
        self.update_robot_status()
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
        for path in (base_path, motor_path):
            if not path.exists():
                raise FileNotFoundError(f"Robot module not found: {path}")

        base_mesh = load_robot_mesh(base_path)
        motor_mesh = load_robot_mesh(motor_path)
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

        # Draw the exact proxy volumes used by Bullet in translucent red so
        # contact judgement can be checked against the CAD wheels visually.
        for wheel_cad, wheel_axis_cad in zip(
            PASSIVE_WHEELS_CAD_MM, PASSIVE_WHEEL_AXES_CAD
        ):
            local = cad_to_sim(wheel_cad - cad_center)
            self.add_contact_marker(
                self.robot_visual_root, local,
                PASSIVE_WHEEL_CONTACT_RADIUS_MM * MM_TO_M,
                PASSIVE_WHEEL_CONTACT_WIDTH_MM * MM_TO_M,
                cad_direction_to_sim(wheel_axis_cad),
            )
        for wheel_module_cad in DRIVE_WHEELS_MODULE_CAD_MM:
            local = cad_to_sim(
                wheel_module_cad + motor_at_min_offset - cad_center
            )
            self.add_contact_marker(
                self.motor_visual, local,
                DRIVE_WHEEL_CONTACT_RADIUS_MM * MM_TO_M,
                DRIVE_WHEEL_CONTACT_WIDTH_MM * MM_TO_M,
                np.array([0.0, 1.0, 0.0]),
            )

        center_x = float((self.bounds[0, 0] + self.bounds[1, 0]) * 0.5)
        self.robot_root.setPos(center_x, 0.0, 0.0)
        self.robot_cad_center_mm = cad_center
        for wheel_cad, wheel_axis_cad in zip(
            PASSIVE_WHEELS_CAD_MM, PASSIVE_WHEEL_AXES_CAD
        ):
            local = cad_to_sim(wheel_cad - cad_center)
            wheel_axis = cad_direction_to_sim(wheel_axis_cad)
            shape = BulletCylinderShape(
                PASSIVE_WHEEL_CONTACT_RADIUS_MM * MM_TO_M,
                PASSIVE_WHEEL_CONTACT_WIDTH_MM * MM_TO_M,
                YUp,
            )
            self.robot_body.addShape(
                shape,
                TransformState.makePos(Vec3(*local.tolist())).compose(
                    TransformState.makeQuat(quaternion_from_y_axis(wheel_axis))
                ),
            )
        self.rebuild_drive_contact_shapes()

    def add_contact_marker(self, parent: NodePath, position: np.ndarray,
                           radius_m: float, width_m: float,
                           axis_sim: np.ndarray) -> None:
        # Use the same Y-axis cylinder envelope visually and in Bullet.
        mesh = trimesh.creation.cylinder(radius=radius_m, height=width_m, sections=32)
        mesh.apply_transform(trimesh.transformations.rotation_matrix(
            np.pi / 2.0, [1.0, 0.0, 0.0]
        ))
        marker_node = GeomNode("wheel_contact_proxy")
        marker_node.addGeom(make_geom(
            "wheel_contact_proxy",
            np.asarray(mesh.vertices), np.asarray(mesh.vertex_normals),
            np.asarray(mesh.faces, dtype=np.int32),
        ))
        marker = parent.attachNewNode(marker_node)
        marker.setPos(*position.tolist())
        marker.setQuat(quaternion_from_y_axis(axis_sim))
        marker.setTwoSided(True)
        marker.setTransparency(TransparencyAttrib.MAlpha)
        marker.setColor(1.0, 0.02, 0.02, 0.58, 10)

    def rebuild_drive_contact_shapes(self) -> None:
        for shape in self.drive_contact_shapes:
            self.robot_body.removeShape(shape)
        self.drive_contact_shapes.clear()
        offset = ROBOT_MODULE_POSITION_MM + np.array([0.0, self.slider6_mm, 0.0])
        for wheel_module_cad in DRIVE_WHEELS_MODULE_CAD_MM:
            local = cad_to_sim(wheel_module_cad + offset - self.robot_cad_center_mm)
            shape = BulletCylinderShape(
                DRIVE_WHEEL_CONTACT_RADIUS_MM * MM_TO_M,
                DRIVE_WHEEL_CONTACT_WIDTH_MM * MM_TO_M,
                YUp,
            )
            self.robot_body.addShape(
                shape, TransformState.makePos(Vec3(*local.tolist()))
            )
            self.drive_contact_shapes.append(shape)
        self.robot_body.setActive(True)

    def update_robot_status(self) -> None:
        orientation = "inverted (drive wheels down)" if self.robot_inverted else "normal"
        self.robot_status.setText(
            f"Robot: {orientation} / cylinder {self.slider6_mm:.0f} mm"
            f" / roll {self.robot_root.getP():+.1f} deg / 8 wheel contact proxies"
        )

    def change_slider6(self, delta_mm: float) -> None:
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

    def toggle_robot_inversion(self) -> None:
        self.robot_inverted = not self.robot_inverted
        # Panda3D pitch is rotation about X, which is the pipe axis here.
        self.robot_root.setP(180.0 if self.robot_inverted else 0.0)
        self.robot_body.setLinearVelocity(Vec3(0, 0, 0))
        self.robot_body.setAngularVelocity(Vec3(0, 0, 0))
        self.robot_body.setActive(True)
        self.update_robot_status()

    def reset_robot(self) -> None:
        center_x = float((self.bounds[0, 0] + self.bounds[1, 0]) * 0.5)
        self.slider6_mm = SLIDER6_MIN_MM
        if self.motor_visual is not None:
            self.motor_visual.setZ(0.0)
        self.rebuild_drive_contact_shapes()
        self.robot_root.setPos(center_x, 0.0, 0.0)
        self.robot_root.setHpr(0.0, 180.0 if self.robot_inverted else 0.0, 0.0)
        self.robot_body.setLinearVelocity(Vec3(0, 0, 0))
        self.robot_body.setAngularVelocity(Vec3(0, 0, 0))
        self.robot_body.clearForces()
        self.robot_body.setActive(True)
        self.update_robot_status()

    def reset_sphere(self) -> None:
        center_x = float((self.bounds[0, 0] + self.bounds[1, 0]) * 0.5)
        self.sphere_np.setPos(center_x, 0.0, 0.02)
        self.sphere_np.node().setLinearVelocity(Vec3(0, 0, 0))
        self.sphere_np.node().setAngularVelocity(Vec3(0, 0, 0))
        self.sphere_np.node().setActive(True)
        self.sphere_np.show()

    def toggle_pause(self) -> None:
        self.paused = not self.paused

    def update(self, task):
        self.update_camera_drag()
        if not self.paused:
            dt = min(globalClock.getDt(), 1.0 / 30.0)
            self.world.doPhysics(dt, 4, 1.0 / 240.0)
            self.update_robot_status()
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
        """Capture side, oblique, and end views for proxy/CAD alignment QA."""
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
            self.capture(output_dir / f"passive_wheel_fit_{name}.png")


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
    args = parser.parse_args()
    mesh = args.mesh.resolve()
    if not mesh.exists():
        raise SystemExit(f"Mesh not found: {mesh}. Run build_pipe_model.py first.")
    if args.headless_test:
        raise SystemExit(run_headless(mesh))
    if args.capture_preview or args.capture_fit_views:
        app = MeasuredPipeApp(mesh, args.robot_assets.resolve(), offscreen=True)
        if args.capture_preview:
            app.capture(args.capture_preview.resolve())
        if args.capture_fit_views:
            app.capture_fit_views(args.capture_fit_views.resolve())
        return
    MeasuredPipeApp(mesh, args.robot_assets.resolve()).run()


if __name__ == "__main__":
    main()
