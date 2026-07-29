from __future__ import annotations

import argparse
import json
import math
import struct
from pathlib import Path


TRIANGLE = struct.Struct("<12fH")


def occurrence_by_path(export: dict, full_path: str) -> dict:
    for occurrence in export.get("occurrences", []):
        if occurrence.get("full_path") == full_path:
            return occurrence
    raise ValueError(f"Fusion occurrence not found: {full_path}")


def transform_point(point: tuple[float, float, float], transform: dict) -> tuple[float, float, float]:
    origin = transform["origin_mm"]
    axes = [transform["x_axis"], transform["y_axis"], transform["z_axis"]]
    return tuple(
        origin[row]
        + axes[0][row] * point[0]
        + axes[1][row] * point[1]
        + axes[2][row] * point[2]
        for row in range(3)
    )


def transform_normal(normal: tuple[float, float, float], transform: dict) -> tuple[float, float, float]:
    axes = [transform["x_axis"], transform["y_axis"], transform["z_axis"]]
    result = [
        axes[0][row] * normal[0]
        + axes[1][row] * normal[1]
        + axes[2][row] * normal[2]
        for row in range(3)
    ]
    length = math.sqrt(sum(value * value for value in result))
    if length > 1e-12:
        result = [value / length for value in result]
    return tuple(result)


def triangle_count(path: Path) -> int:
    with path.open("rb") as handle:
        header = handle.read(84)
    if len(header) != 84:
        raise ValueError(f"STL header is too short: {path}")
    count = struct.unpack_from("<I", header, 80)[0]
    if path.stat().st_size != 84 + count * TRIANGLE.size:
        raise ValueError(f"Only binary STL files are supported: {path}")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply Fusion occurrence transforms and combine module STL files."
    )
    parser.add_argument("fusion_export", type=Path)
    parser.add_argument("output_stl", type=Path)
    parser.add_argument("output_map", type=Path)
    parser.add_argument(
        "module",
        nargs="+",
        help='Mapping in the form "Fusion occurrence full path=STL path".',
    )
    args = parser.parse_args()

    export = json.loads(args.fusion_export.read_text(encoding="utf-8"))
    modules: list[tuple[str, Path, dict, int]] = []
    for mapping in args.module:
        occurrence_path, separator, stl_text = mapping.partition("=")
        if not separator:
            raise ValueError(f"Invalid module mapping: {mapping}")
        stl_path = Path(stl_text)
        modules.append(
            (
                occurrence_path,
                stl_path,
                occurrence_by_path(export, occurrence_path)["transform"],
                triangle_count(stl_path),
            )
        )

    total = sum(item[3] for item in modules)
    args.output_stl.parent.mkdir(parents=True, exist_ok=True)
    args.output_map.parent.mkdir(parents=True, exist_ok=True)
    minimum = [float("inf")] * 3
    maximum = [float("-inf")] * 3

    with args.output_stl.open("wb") as output:
        output.write(b"Haikan Fusion-positioned assembly".ljust(80, b"\0"))
        output.write(struct.pack("<I", total))
        for occurrence_path, stl_path, transform, count in modules:
            with stl_path.open("rb") as source:
                source.seek(84)
                for _ in range(count):
                    values = TRIANGLE.unpack(source.read(TRIANGLE.size))
                    normal = transform_normal(values[0:3], transform)
                    vertices = [
                        transform_point(values[index:index + 3], transform)
                        for index in (3, 6, 9)
                    ]
                    for vertex in vertices:
                        for axis in range(3):
                            minimum[axis] = min(minimum[axis], vertex[axis])
                            maximum[axis] = max(maximum[axis], vertex[axis])
                    output.write(
                        TRIANGLE.pack(
                            *normal,
                            *vertices[0],
                            *vertices[1],
                            *vertices[2],
                            values[12],
                        )
                    )
            print(f"{occurrence_path}: {stl_path.name} triangles={count}")

    with args.output_map.open("wb") as output:
        output.write(b"HCP1")
        output.write(struct.pack("<II", total, len(modules)))
        for module_id, (_, _, _, count) in enumerate(modules):
            value = struct.pack("<H", module_id)
            block_size = 32768
            for start in range(0, count, block_size):
                output.write(value * min(block_size, count - start))

    print(f"triangles={total} modules={len(modules)}")
    print("minimum_mm=" + ",".join(f"{value:.9g}" for value in minimum))
    print("maximum_mm=" + ",".join(f"{value:.9g}" for value in maximum))
    print(args.output_stl)
    print(args.output_map)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
