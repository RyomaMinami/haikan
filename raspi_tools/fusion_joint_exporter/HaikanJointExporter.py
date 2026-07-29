from __future__ import annotations

import json
import math
import traceback
from pathlib import Path

import adsk.core
import adsk.fusion


OUTPUT_PATH = Path.home() / "Downloads" / "haikan_fusion_joints.json"


def collection_items(collection):
    if collection is None:
        return []
    try:
        return [collection.item(index) for index in range(collection.count)]
    except Exception:
        return list(collection)


def point_mm(point):
    if point is None:
        return None
    return [point.x * 10.0, point.y * 10.0, point.z * 10.0]


def vector_values(vector):
    if vector is None:
        return None
    return [vector.x, vector.y, vector.z]


def matrix_data(matrix):
    if matrix is None:
        return None
    origin, x_axis, y_axis, z_axis = matrix.getAsCoordinateSystem()
    return {
        "origin_mm": point_mm(origin),
        "x_axis": vector_values(x_axis),
        "y_axis": vector_values(y_axis),
        "z_axis": vector_values(z_axis),
        "matrix": list(matrix.asArray()),
    }


def occurrence_name(occurrence):
    return occurrence.fullPathName if occurrence else None


def limits_data(limits, angular):
    if limits is None:
        return None

    def converted(value):
        return math.degrees(value) if angular else value * 10.0

    return {
        "unit": "deg" if angular else "mm",
        "minimum_enabled": bool(limits.isMinimumValueEnabled),
        "minimum": converted(limits.minimumValue),
        "maximum_enabled": bool(limits.isMaximumValueEnabled),
        "maximum": converted(limits.maximumValue),
        "rest_enabled": bool(limits.isRestValueEnabled),
        "rest": converted(limits.restValue),
    }


def motion_data(motion):
    if motion is None:
        return {"type": "unknown"}
    object_type = motion.objectType
    result = {"fusion_object_type": object_type}
    if object_type == adsk.fusion.RevoluteJointMotion.classType():
        result.update(
            {
                "type": "revolute",
                "axis": vector_values(motion.rotationAxisVector),
                "limits": limits_data(motion.rotationLimits, True),
            }
        )
    elif object_type == adsk.fusion.SliderJointMotion.classType():
        result.update(
            {
                "type": "prismatic",
                "axis": vector_values(motion.slideDirectionVector),
                "limits": limits_data(motion.slideLimits, False),
            }
        )
    elif object_type == adsk.fusion.RigidJointMotion.classType():
        result["type"] = "rigid"
    elif object_type == adsk.fusion.CylindricalJointMotion.classType():
        result.update(
            {
                "type": "cylindrical",
                "axis": vector_values(motion.rotationAxisVector),
                "rotation_limits": limits_data(motion.rotationLimits, True),
                "slide_limits": limits_data(motion.slideLimits, False),
            }
        )
    else:
        result["type"] = object_type.rsplit("::", 1)[-1]
    return result


def joint_data(joint, kind):
    if kind == "as_built":
        transform = getattr(joint, "transform", None)
    else:
        transform = getattr(joint, "geometryOneTransform", None)
    return {
        "name": joint.name,
        "kind": kind,
        "occurrence_one": occurrence_name(joint.occurrenceOne),
        "occurrence_two": occurrence_name(joint.occurrenceTwo),
        "transform": matrix_data(transform),
        "motion": motion_data(joint.jointMotion),
        "suppressed": bool(getattr(joint, "isSuppressed", False)),
        "locked": bool(getattr(joint, "isLocked", False)),
        "health": str(getattr(joint, "healthState", "")),
        "warning": str(getattr(joint, "errorOrWarningMessage", "")),
    }


def occurrence_data(occurrence):
    box = occurrence.boundingBox
    return {
        "name": occurrence.name,
        "full_path": occurrence.fullPathName,
        "component": occurrence.component.name,
        "referenced": bool(occurrence.isReferencedComponent),
        "transform": matrix_data(occurrence.transform2),
        "bounding_box_mm": {
            "minimum": point_mm(box.minPoint),
            "maximum": point_mm(box.maxPoint),
        },
    }


def run(_context):
    app = adsk.core.Application.get()
    ui = app.userInterface
    try:
        design = adsk.fusion.Design.cast(app.activeProduct)
        if design is None:
            raise RuntimeError("デザイン作業スペースでジョイント設定済みモデルを開いてください。")

        root = design.rootComponent
        joints = [
            joint_data(joint, "joint")
            for joint in collection_items(root.allJoints)
        ]
        as_built_joints = [
            joint_data(joint, "as_built")
            for joint in collection_items(root.allAsBuiltJoints)
        ]
        occurrences = [
            occurrence_data(occurrence)
            for occurrence in collection_items(root.allOccurrences)
        ]
        payload = {
            "format": "haikan-fusion-joints",
            "version": 1,
            "document": app.activeDocument.name,
            "fusion_internal_length_unit": "cm",
            "exported_length_unit": "mm",
            "joints": joints + as_built_joints,
            "occurrences": occurrences,
        }
        OUTPUT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ui.messageBox(
            f"ジョイント情報を書き出しました。\n\n"
            f"通常ジョイント: {len(joints)}\n"
            f"位置固定ジョイント: {len(as_built_joints)}\n"
            f"保存先: {OUTPUT_PATH}"
        )
    except Exception:
        ui.messageBox("書き出しに失敗しました。\n\n" + traceback.format_exc())


def stop(_context):
    pass
